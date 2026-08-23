"""Full-history bulk sweep of the eCourts dataset via tar streaming.

The nightly IndiaCourts lanes walk ~500 PDFs/cycle — fine for keeping
current, hopeless for the ~18.5M-judgment backlog to the 2000 floor. This
job streams the dataset's per-bench-year tar bundles
(``data/tar/year=Y/court=C/bench=B/data.tar``) member by member — no tar
ever touches disk — scans each judgment with the same lexicon, and spools
matches as per-partition chunk files that the nightly cycle merges
idempotently (link-deduped by the raw store). Venue-agnostic by design: it
runs identically in the capped ``sweep`` compose service on sparky or on a
throwaway cloud CPU VM whose spool rsyncs to the corpus bucket.

Coordination rules:
- NEVER writes the main raw store — chunks only (``merge_sweep_spool`` on
  the nightly side is the single writer).
- Pauses while the spark refresh flock is held, so the nightly cycle always
  has the box to itself.
- Marks swept partitions complete in the SAME PartitionState the nightly
  walkers read, so forward/history passes skip swept ground. Current and
  previous years record the full done-set (the forward differ re-walks on
  every daily etag change); older years store a bare complete marker.
- Zero GPU, zero LLM: matches enter the corpus un-enriched and the normal
  enrichment pipeline bills them (or not) exactly like any other filing.
"""

from __future__ import annotations

import fcntl
import json
import logging
import multiprocessing
import os
import tarfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from concurrent.futures.process import BrokenProcessPool
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

import httpx

from apps.aggregator.indiacourts import (
    IndiaCourtsError,
    JudgmentMeta,
    PartitionRef,
    detect_language,
    fetch_bytes,
    judgment_to_raw_article,
    list_partitions,
    pdf_bytes_to_text,
    read_partition_metadata,
    scan_insider_patterns,
    truncate_head_tail,
)
from apps.aggregator.indiacourts_pipeline import (
    DEFAULT_INDIACOURTS_STATE_DIR,
    PartitionState,
    _scoped_court_order,
    _sort_partitions,
    resolve_ocr_backend,
)
from shared.settings import get_settings

logger = logging.getLogger(__name__)

DEFAULT_SPOOL_DIR = "data/raw/sweep_spool"
REFRESH_LOCK_PATH = "/tmp/insider-intel-spark-refresh.lock"
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"

# Worker threads fold their local counters into the shared stats every
# FLUSH_EVERY pdfs, and each time the GLOBAL pdf count crosses a
# PROGRESS_LOG_EVERY boundary the crossing thread logs a rate line and
# rewrites status.json — so docker-logs tails and the bucket heartbeat carry
# a live pulse even while every worker sits inside a 100k-member tar (the
# first live run was blind for 8h because all telemetry waited for a
# partition to complete).
FLUSH_EVERY = 50
PROGRESS_LOG_EVERY = 2000
EXTRACT_TIMEOUT_SECONDS = 300.0


def refresh_lock_held(path: str | None = None) -> bool:
    """True while a spark refresh cycle holds its flock (sweep must pause).

    In the sweep container the host's /tmp is bind-mounted read-only and
    SPARK_REFRESH_LOCK_PATH points at the lock inside it; on a cloud VM the
    path never exists and the sweep simply never pauses.
    """
    lock = Path(path or os.environ.get("SPARK_REFRESH_LOCK_PATH", REFRESH_LOCK_PATH))
    if not lock.exists():
        return False
    try:
        with lock.open() as fh:
            fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(fh, fcntl.LOCK_UN)
        return False
    except OSError:
        return True


def list_tar_partitions(client: httpx.Client, *, base_url: str, year: int) -> list[PartitionRef]:
    """Enumerate ``data/tar/year=Y/**/data.tar`` bundles as PartitionRefs."""
    refs: list[PartitionRef] = []
    token: str | None = None
    while True:
        params = {"list-type": "2", "prefix": f"data/tar/year={year}/", "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        resp = client.get(base_url, params=params)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.text)
        for item in root.findall(f"{_NS}Contents"):
            key = item.findtext(f"{_NS}Key") or ""
            if not key.endswith("/data.tar"):
                continue
            try:
                court = key.split("court=")[1].split("/")[0]
                bench = key.split("bench=")[1].split("/")[0]
            except IndexError:
                continue
            etag = (item.findtext(f"{_NS}ETag") or "").strip('"').replace("&quot;", "")
            refs.append(PartitionRef(year=year, court=court, bench=bench, etag=etag))
        nxt = root.findtext(f"{_NS}NextContinuationToken")
        if not nxt:
            break
        token = nxt
    return refs


class _StreamReader:
    """Minimal file-like over an httpx byte iterator for tarfile 'r|' mode."""

    def __init__(self, iterator) -> None:
        self._it = iterator
        self._buf = b""

    def read(self, n: int = -1) -> bytes:
        while n < 0 or len(self._buf) < n:
            try:
                self._buf += next(self._it)
            except StopIteration:
                break
        if n < 0:
            out, self._buf = self._buf, b""
        else:
            out, self._buf = self._buf[:n], self._buf[n:]
        return out


class _SweepStats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started_monotonic = time.monotonic()
        self.partitions_done = 0
        self.pdfs = 0
        self.matches = 0
        self.corrupt = 0
        self.scanned_skipped = 0
        self.ocr_used = 0
        self.non_english = 0
        self.extract_timeouts = 0
        self.errors = 0

    def bump(
        self,
        *,
        pdfs: int = 0,
        matches: int = 0,
        corrupt: int = 0,
        scanned_skipped: int = 0,
        ocr_used: int = 0,
        non_english: int = 0,
        extract_timeouts: int = 0,
    ) -> int:
        """Fold worker-local deltas in mid-partition; returns the global pdf
        count so the caller can detect a progress-log boundary crossing."""
        with self.lock:
            self.pdfs += pdfs
            self.matches += matches
            self.corrupt += corrupt
            self.scanned_skipped += scanned_skipped
            self.ocr_used += ocr_used
            self.non_english += non_english
            self.extract_timeouts += extract_timeouts
            return self.pdfs

    def snapshot(self) -> dict:
        with self.lock:
            return {k: v for k, v in self.__dict__.items() if isinstance(v, int)}


class _PartitionCounts:
    """Per-partition tallies that flush into the shared stats incrementally.

    Keeps two views: lifetime totals (for the partition-completion log line)
    and the un-flushed remainder (folded into _SweepStats every FLUSH_EVERY
    pdfs and once more at partition end, so nothing double-counts)."""

    FIELDS = (
        "pdfs",
        "matches",
        "corrupt",
        "scanned_skipped",
        "ocr_used",
        "non_english",
        "extract_timeouts",
    )

    def __init__(self) -> None:
        for f in self.FIELDS:
            setattr(self, f, 0)
            setattr(self, f + "_flushed", 0)

    def flush(self, stats: _SweepStats) -> tuple[int, int]:
        """Push the un-flushed remainder; returns (global pdfs, pdf delta)."""
        deltas = {f: getattr(self, f) - getattr(self, f + "_flushed") for f in self.FIELDS}
        for f in self.FIELDS:
            setattr(self, f + "_flushed", getattr(self, f))
        return stats.bump(**deltas), deltas["pdfs"]


class _ExtractPool:
    """pdf_bytes_to_text on a spawn-context process pool.

    pypdf extraction is (almost) pure Python, so running it on the streaming
    threads caps aggregate throughput near ONE core no matter how many
    workers — the first live VM run finished zero partitions in 8h on 8
    threads / 16 vCPUs. Processes sidestep the GIL; spawn (not fork) because
    the parent is heavily threaded. Self-heals a broken pool once per call
    (a segfaulting child must never quietly end days of run time)."""

    def __init__(self, procs: int) -> None:
        self._procs = procs
        self._lock = threading.Lock()
        self._pool = self._new_pool()

    def _new_pool(self) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(
            max_workers=self._procs, mp_context=multiprocessing.get_context("spawn")
        )

    def run(self, data: bytes, max_chars: int) -> str:
        """Extract text; raises IndiaCourtsError / FutureTimeoutError upward."""
        for attempt in (0, 1):
            with self._lock:
                pool = self._pool
            try:
                fut = pool.submit(pdf_bytes_to_text, data, max_chars=max_chars)
                return fut.result(timeout=EXTRACT_TIMEOUT_SECONDS)
            except BrokenProcessPool:
                if attempt:
                    raise IndiaCourtsError("extraction pool broke twice in a row") from None
                logger.warning("[india-sweep] extraction pool broke — rebuilding")
                with self._lock:
                    if self._pool is pool:  # first thread in rebuilds; the rest reuse
                        self._pool.shutdown(wait=False, cancel_futures=True)
                        self._pool = self._new_pool()
        raise IndiaCourtsError("unreachable")  # pragma: no cover

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def _tar_key(ref: PartitionRef) -> str:
    return f"data/tar/year={ref.year}/court={ref.court}/bench={ref.bench}/data.tar"


def _sweep_partition(
    ref: PartitionRef,
    *,
    client: httpx.Client,
    settings,
    ocr,
    spool: Path,
    state_dir: Path,
    stats: _SweepStats,
    record_names: bool,
    parquet_etag: str = "",
    extract: _ExtractPool | None = None,
) -> None:
    """Stream one bench-year tar; spool matches; mark the partition swept."""
    started = time.monotonic()
    # The completion marker must carry the PARQUET etag — that is what the
    # nightly forward/history walkers compare before skipping a partition.
    parquet_ref = PartitionRef(year=ref.year, court=ref.court, bench=ref.bench, etag=parquet_etag)
    try:
        parquet = fetch_bytes(
            client,
            parquet_ref.metadata_key,
            base_url=settings.indiacourts_base_url,
            max_bytes=200_000_000,
        )
        metas = read_partition_metadata(parquet, parquet_ref)
    except IndiaCourtsError as exc:
        with stats.lock:
            stats.errors += 1
        logger.warning(
            "[india-sweep] %s/%s/%s parquet failed: %s", ref.year, ref.court, ref.bench, exc
        )
        return
    by_basename: dict[str, JudgmentMeta] = {m.pdf_basename: m for m in metas}

    rows: list[str] = []
    counts = _PartitionCounts()
    url = f"{settings.indiacourts_base_url.rstrip('/')}/{_tar_key(ref)}"
    with client.stream("GET", url) as resp:
        if resp.status_code != 200:
            with stats.lock:
                stats.errors += 1
            logger.warning("[india-sweep] tar HTTP %s for %s", resp.status_code, _tar_key(ref))
            return
        reader = _StreamReader(resp.iter_raw())
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for member in tar:
                if not member.isfile() or not member.name.endswith(".pdf"):
                    continue
                basename = member.name.rsplit("/", 1)[-1]
                meta = by_basename.get(basename)
                if meta is None or member.size > settings.indiacourts_pdf_max_bytes:
                    continue
                fh = tar.extractfile(member)
                if fh is None:
                    continue
                data = fh.read()
                counts.pdfs += 1
                if counts.pdfs % FLUSH_EVERY == 0:
                    total, delta = counts.flush(stats)
                    if total // PROGRESS_LOG_EVERY != (total - delta) // PROGRESS_LOG_EVERY:
                        _log_progress(stats, spool, ref.year)
                if b"%PDF" not in data[:1024]:
                    counts.corrupt += 1
                    continue
                try:
                    if extract is not None:
                        text = extract.run(data, settings.indiacourts_text_max_chars)
                    else:
                        text = pdf_bytes_to_text(
                            data, max_chars=settings.indiacourts_text_max_chars
                        )
                except FutureTimeoutError:
                    counts.extract_timeouts += 1
                    text = ""
                except IndiaCourtsError:
                    text = ""
                if len(text) < settings.indiacourts_min_text_chars and ocr is not None:
                    try:
                        text = truncate_head_tail(
                            ocr(data).strip(), settings.indiacourts_text_max_chars
                        )
                        counts.ocr_used += 1
                    except Exception:  # noqa: BLE001 — OCR must never kill the sweep
                        pass
                if len(text) < settings.indiacourts_min_text_chars:
                    counts.scanned_skipped += 1
                    continue
                if detect_language(text) == "hi":
                    counts.non_english += 1
                matched = scan_insider_patterns(text)
                if not matched:
                    continue
                counts.matches += 1
                article = judgment_to_raw_article(
                    meta, matched, text, base_url=settings.indiacourts_base_url
                )
                rows.append(article.model_dump_json())

    if rows:
        chunk = spool / f"{ref.year}_{ref.court}_{ref.bench}.jsonl"
        tmp = chunk.with_suffix(".tmp")
        tmp.write_text("\n".join(rows) + "\n", encoding="utf-8")
        os.replace(tmp, chunk)

    state = PartitionState(state_dir, parquet_ref)
    state.etag = parquet_ref.etag or state.etag
    if record_names:
        state.done.update(by_basename)
    state.complete = True
    state.save()

    counts.flush(stats)
    with stats.lock:
        stats.partitions_done += 1
    # Per-PARTITION status write: per-year alone left hours-long blind windows
    # on big years (first live check, 2026-08-23) — monitoring needs a pulse.
    _write_status(spool, stats, stats.started_monotonic, ref.year)
    logger.info(
        "[india-sweep] %s/%s/%s: pdfs=%d matches=%d corrupt=%d scanned_skipped=%d "
        "ocr=%d non_english=%d in %.0fs",
        ref.year,
        ref.court,
        ref.bench,
        counts.pdfs,
        counts.matches,
        counts.corrupt,
        counts.scanned_skipped,
        counts.ocr_used,
        counts.non_english,
        time.monotonic() - started,
    )


def _log_progress(stats: _SweepStats, spool: Path, year: int) -> None:
    """Mid-partition pulse: a rate line for the heartbeat + a status rewrite."""
    snap = stats.snapshot()
    elapsed = max(1.0, time.monotonic() - stats.started_monotonic)
    logger.info(
        "[india-sweep] progress: pdfs=%d (%.0f/h) matches=%d corrupt=%d "
        "scanned_skipped=%d ocr=%d non_english=%d timeouts=%d partitions_done=%d",
        snap["pdfs"],
        snap["pdfs"] / elapsed * 3600,
        snap["matches"],
        snap["corrupt"],
        snap["scanned_skipped"],
        snap["ocr_used"],
        snap["non_english"],
        snap["extract_timeouts"],
        snap["partitions_done"],
    )
    _write_status(spool, stats, stats.started_monotonic, year)


def run_indiacourts_bulk_sweep(
    *,
    spool_dir: str | None = None,
    state_dir: str = DEFAULT_INDIACOURTS_STATE_DIR,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> dict:
    """Sweep every bench-year tar from the current year back to the floor.

    Resumable: a partition whose PartitionState is complete with an unchanged
    parquet etag is skipped, so restarts (spot preemption, reboot) lose at
    most the in-flight partitions.
    """
    settings = get_settings()
    now = now or datetime.now(UTC)
    floor = int((settings.indiacourts_history_floor or "2000").split("-")[0])
    spool = Path(spool_dir or settings.indiacourts_sweep_spool_dir)
    spool.mkdir(parents=True, exist_ok=True)
    sdir = Path(state_dir)
    sdir.mkdir(parents=True, exist_ok=True)
    stats = _SweepStats()
    ocr = resolve_ocr_backend(settings) if settings.indiacourts_sweep_ocr else None
    order, scope = _scoped_court_order(settings)
    workers = max(1, int(settings.indiacourts_sweep_workers))
    procs = max(0, int(settings.indiacourts_sweep_extract_procs))
    extract = _ExtractPool(procs) if procs else None
    if extract is not None:
        logger.info("[india-sweep] extraction on a %d-process pool", procs)
    own_client = client is None
    http = client or httpx.Client(timeout=120.0)
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for year in range(now.year, floor - 1, -1):
                try:
                    refs = list_tar_partitions(
                        http, base_url=settings.indiacourts_base_url, year=year
                    )
                except (httpx.HTTPError, ElementTree.ParseError) as exc:
                    with stats.lock:
                        stats.errors += 1
                    logger.warning("[india-sweep] year %d listing failed: %s", year, exc)
                    continue
                todo = []
                for ref in _sort_partitions(refs, order, scope):
                    parquet_ref = PartitionRef(year=ref.year, court=ref.court, bench=ref.bench)
                    state = PartitionState(sdir, parquet_ref)
                    if state.complete:
                        continue
                    todo.append(ref)
                if not todo:
                    continue
                try:
                    parquet_etags = {
                        (p.court, p.bench): p.etag
                        for p in list_partitions(
                            http, base_url=settings.indiacourts_base_url, year=year
                        )
                    }
                except IndiaCourtsError:
                    parquet_etags = {}
                logger.info("[india-sweep] year %d: %d partition(s) to sweep", year, len(todo))
                futures = []
                for ref in todo:
                    while refresh_lock_held():
                        logger.info("[india-sweep] refresh cycle running — pausing 120s")
                        time.sleep(120)
                    futures.append(
                        pool.submit(
                            _sweep_partition,
                            ref,
                            client=http,
                            settings=settings,
                            ocr=ocr,
                            spool=spool,
                            state_dir=sdir,
                            stats=stats,
                            record_names=year >= now.year - 1,
                            parquet_etag=parquet_etags.get((ref.court, ref.bench), ""),
                            extract=extract,
                        )
                    )
                for fut in futures:
                    fut.result()
                _write_status(spool, stats, started, year)
    finally:
        if extract is not None:
            extract.close()
        if own_client:
            http.close()
    summary = stats.snapshot()
    summary["elapsed_seconds"] = int(time.monotonic() - started)
    _write_status(spool, stats, started, floor)
    logger.info("[india-sweep] DONE: %s", summary)
    return summary


def _write_status(spool: Path, stats: _SweepStats, started: float, year: int) -> None:
    """Progress file for the sweep-status op (and post-mortems).

    Called per partition from worker threads — the tmp name is per-thread so
    concurrent writers never trample each other's rename."""
    payload = stats.snapshot()
    payload["current_year"] = year
    payload["elapsed_seconds"] = int(time.monotonic() - started)
    payload["pdfs_per_hour"] = int(payload["pdfs"] / max(1, payload["elapsed_seconds"]) * 3600)
    payload["updated_at"] = datetime.now(UTC).isoformat()
    tmp = spool / f".status.{threading.get_ident()}.tmp"
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, spool / "status.json")
