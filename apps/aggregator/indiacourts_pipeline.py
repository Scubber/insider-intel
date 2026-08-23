"""Ingest jobs for the Indian High Court open-dataset lane.

Three bounded jobs, all resumable and all $0 (AWS-sponsored dataset, local
extraction and scanning):

* ``run_indiacourts_ingestion`` — forward: diff current+previous-year
  partitions against stored state (parquet ETag + per-PDF done-sets), fetch
  and scan only new judgments.
* ``run_indiacourts_history_sweep`` — a year cursor walks backward to the
  configured floor, finishing every partition of a year (hub courts first)
  before advancing; per-run caps make each refresh a bounded slice.
* ``run_indiacourts_extract_pending`` — retries PDFs that failed fetch/parse
  or need OCR, after a cool-down, via the optional OCR command backend.

Design divergence from CourtListener (recorded in docs/india-courts-ingest.md):
matching requires the text, so there are NO metadata-only stub rows — a
judgment is stored only when its text matched the lexicon, with the text
already attached. Nothing here ever re-enriches an existing row, so the
CL backfill's ``_clear_llm_fields`` contract does not apply.

State lives under ``data/state/indiacourts/``: one JSON per partition
(``{year}_{court}_{bench}.json`` → etag + done basenames + complete flag),
plus ``pending.json`` (the retry queue) and the history cursor in the shared
``JsonIngestState``. All writes are atomic (tmp + replace).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from apps.aggregator.indiacourts import (
    SOURCE_ID,
    SOURCE_NAME,
    IndiaCourtsError,
    JudgmentMeta,
    PartitionRef,
    command_ocr_backend,
    court_path,
    fetch_bytes,
    judgment_to_raw_article,
    list_partitions,
    meta_from_pending,
    pdf_bytes_to_text,
    pending_entry,
    read_partition_metadata,
    scan_insider_patterns,
)
from apps.aggregator.ingest_state import DEFAULT_STATE_PATH, JsonIngestState
from apps.aggregator.pipeline import DEFAULT_STORE_PATH
from apps.aggregator.storage import ArticleStore, JsonlArticleStore
from shared.schemas.articles import (
    IngestionRunResult,
    RawArticle,
    SourceIngestionResult,
)
from shared.settings import get_settings

logger = logging.getLogger(__name__)

DEFAULT_INDIACOURTS_STATE_DIR = "data/state/indiacourts"
_HISTORY_CURSOR_KEY = "indiacourts_history:cursor"
_PENDING_NAME = "pending.json"
# Spaced retries before a pending PDF is dead-lettered (oversized/corrupt/gone
# documents must not consume extract budget forever).
_MAX_PENDING_ATTEMPTS = 5

OcrBackend = Callable[[bytes], str]


# ---------------------------------------------------------------------------
# Partition + pending state (JSON files under data/state/indiacourts/)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring unreadable state %s: %s", path, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


class PartitionState:
    """Per-partition progress: parquet ETag, processed basenames, completion."""

    def __init__(self, state_dir: Path, partition: PartitionRef) -> None:
        self.path = state_dir / f"{partition.state_name}.json"
        payload = _read_json(self.path)
        self.etag: str = str(payload.get("etag") or "")
        self.done: set[str] = {str(b) for b in payload.get("done") or []}
        self.complete: bool = bool(payload.get("complete"))

    def save(self) -> None:
        _write_json(
            self.path,
            {"etag": self.etag, "done": sorted(self.done), "complete": self.complete},
        )


class PendingQueue:
    """Retry queue for PDFs that failed fetch/parse or await OCR."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / _PENDING_NAME
        self._items: dict[str, dict[str, Any]] = {
            str(k): v for k, v in _read_json(self.path).items() if isinstance(v, dict)
        }

    def add(self, pdf_key: str, entry: dict[str, Any], *, now: datetime) -> None:
        existing = self._items.get(pdf_key) or {}
        entry = dict(entry)
        entry["attempts"] = int(existing.get("attempts") or 0) + 1
        entry["last"] = now.isoformat()
        self._items[pdf_key] = entry
        self.save()

    def remove(self, pdf_key: str) -> None:
        if pdf_key in self._items:
            del self._items[pdf_key]
            self.save()

    def due(self, *, now: datetime, retry_days: float) -> list[tuple[str, dict[str, Any]]]:
        cutoff = now - timedelta(days=retry_days)
        out: list[tuple[str, dict[str, Any]]] = []
        for key, entry in sorted(self._items.items()):
            try:
                last = datetime.fromisoformat(str(entry.get("last")))
            except (TypeError, ValueError):
                last = None
            if last is None or last <= cutoff:
                out.append((key, entry))
        return out

    def __len__(self) -> int:
        return len(self._items)

    def save(self) -> None:
        _write_json(self.path, self._items)


# ---------------------------------------------------------------------------
# Court scoping and ordering


def _scoped_court_order(settings) -> tuple[list[str], set[str] | None]:
    """(priority order, scope set or None=all), both in path form."""
    order = [court_path(c) for c in settings.indiacourts_court_order.split(",") if c.strip()]
    scope_raw = [court_path(c) for c in settings.indiacourts_courts.split(",") if c.strip()]
    scope = set(scope_raw) if scope_raw else None
    return order, scope


def _sort_partitions(
    partitions: list[PartitionRef], order: list[str], scope: set[str] | None
) -> list[PartitionRef]:
    """Hub-courts-first, then remaining courts alphabetically, then bench."""
    rank = {code: i for i, code in enumerate(order)}
    kept = [p for p in partitions if scope is None or p.court in scope]
    return sorted(kept, key=lambda p: (rank.get(p.court, len(rank)), p.court, p.bench))


# ---------------------------------------------------------------------------
# Shared fetch → extract → scan → store step


class _Budget:
    def __init__(self, limit: int) -> None:
        self.limit = max(0, int(limit))
        self.spent = 0

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.limit

    def take(self) -> bool:
        if self.exhausted:
            return False
        self.spent += 1
        return True


class _Stats:
    """Bounded operational counters for the run log (never full bodies)."""

    def __init__(self) -> None:
        self.pdfs_attempted = 0
        self.partitions_checked = 0
        self.matches = 0
        self.pending_added = 0
        # Systemic failures only (partition listing/parquet unreadable) — these
        # mean the lane could not do its job and fail the run. Per-document
        # problems (truncated PDF, scanned pages, fetch hiccup) are the
        # pending queue's DESIGNED path and land in doc_issues instead: the
        # first live smoke (2026-08-23) had 5/200 truncated PDFs park cleanly
        # and the run wrongly reported [FAIL] — at that rate every real cycle
        # would trip a false [LANE-BROKEN].
        self.errors: list[str] = []
        self.doc_issues: list[str] = []
        # Documents whose text is non-English (Devanagari-dominant): the
        # English lexicon cannot match them, so this counter is the measured
        # coverage gap that decides whether a Hindi lexicon is ever worth
        # building (survey 2026-08-23: 0/62 sampled PDFs were Hindi).
        self.non_english = 0

    @property
    def work_done(self) -> int:
        """Units examined this run, for lane health.

        An idle forward cycle (every partition ETag unchanged — normal
        whenever the daily dataset hasn't regenerated since the last pass,
        at any refresh cadence) still CHECKED partitions; reporting 0 here
        would classify a healthy lane as "empty" and trip [LANE-BROKEN]
        after 3 quiet cycles.
        """
        return self.pdfs_attempted or self.partitions_checked


def _process_meta(
    meta: JudgmentMeta,
    *,
    client: httpx.Client,
    settings,
    ocr: OcrBackend | None,
    pending: PendingQueue,
    stats: _Stats,
    now: datetime,
) -> tuple[RawArticle | None, bool]:
    """One judgment: fetch PDF → text (OCR fallback) → scan.

    Returns ``(article, extracted)``: ``extracted`` is True when usable text
    was obtained (the scan ran — matched or not); False means the PDF was
    parked in the pending queue for a later retry. Never raises.
    """
    stats.pdfs_attempted += 1
    try:
        data = fetch_bytes(
            client,
            meta.pdf_key,
            base_url=settings.indiacourts_base_url,
            max_bytes=settings.indiacourts_pdf_max_bytes,
        )
    except IndiaCourtsError as exc:
        pending.add(meta.pdf_key, pending_entry(meta, "fetch"), now=now)
        stats.pending_added += 1
        stats.doc_issues.append(str(exc))
        return None, False

    if b"%PDF" not in data[:1024]:
        # Corrupt AT SOURCE: the upstream scraper stored an error body (e.g.
        # a '{"msg...' JSON) under the .pdf key — seen live 2026-08-23. No
        # retry or OCR can fix these; retiring immediately beats burning 5
        # spaced retries per object (matters at 18.5M-judgment sweep scale).
        stats.doc_issues.append(f"not a PDF at source (skipped for good): {meta.pdf_key}")
        return None, True

    text = ""
    try:
        text = pdf_bytes_to_text(data, max_chars=settings.indiacourts_text_max_chars)
    except IndiaCourtsError as exc:
        stats.doc_issues.append(str(exc))
    if len(text) < settings.indiacourts_min_text_chars:
        # Scanned/empty text layer: OCR now when a backend is configured,
        # otherwise park it for extract_indiacourts_pending.
        if ocr is not None:
            try:
                raw_text = ocr(data)
                from apps.aggregator.indiacourts import truncate_head_tail

                text = truncate_head_tail(raw_text.strip(), settings.indiacourts_text_max_chars)
            except Exception as exc:  # noqa: BLE001 — an OCR backend must never
                # kill the refresh run; the PDF parks and retries.
                stats.doc_issues.append(f"ocr: {exc}")
        if len(text) < settings.indiacourts_min_text_chars:
            pending.add(meta.pdf_key, pending_entry(meta, "ocr"), now=now)
            stats.pending_added += 1
            return None, False

    from apps.aggregator.indiacourts import detect_language

    if detect_language(text) == "hi":
        stats.non_english += 1
    matched = scan_insider_patterns(text)
    if not matched:
        return None, True
    stats.matches += 1
    article = judgment_to_raw_article(
        meta, matched, text, base_url=settings.indiacourts_base_url
    )
    return article, True


def _process_partition(
    partition: PartitionRef,
    *,
    client: httpx.Client,
    settings,
    state_dir: Path,
    ocr: OcrBackend | None,
    pending: PendingQueue,
    budget: _Budget,
    stats: _Stats,
    store_batch: list[RawArticle],
    now: datetime,
) -> bool:
    """Process a partition's new judgments; True when the partition is complete.

    Completion means every basename in the CURRENT parquet is in the done-set
    (matched, unmatched, or parked in pending — parked PDFs retry through the
    pending queue, not through partition re-walks).
    """
    state = PartitionState(state_dir, partition)
    if partition.etag and state.etag == partition.etag and state.complete:
        return True

    try:
        parquet = fetch_bytes(
            client,
            partition.metadata_key,
            base_url=settings.indiacourts_base_url,
            max_bytes=50_000_000,
        )
        metas = read_partition_metadata(parquet, partition)
    except IndiaCourtsError as exc:
        stats.errors.append(str(exc))
        return False

    todo = [m for m in metas if m.pdf_basename not in state.done]
    delay = settings.indiacourts_request_delay_seconds
    for meta in todo:
        if not budget.take():
            break
        article, _extracted = _process_meta(
            meta,
            client=client,
            settings=settings,
            ocr=ocr,
            pending=pending,
            stats=stats,
            now=now,
        )
        if article is not None:
            store_batch.append(article)
        # Done either way: parked PDFs retry through the pending queue, not
        # through partition re-walks (keeps partition passes single-shot).
        state.done.add(meta.pdf_basename)
        if delay:
            time.sleep(delay)

    all_names = {m.pdf_basename for m in metas}
    state.etag = partition.etag or state.etag
    state.complete = all_names <= state.done
    state.save()
    return state.complete


def _finish(
    result_id: str,
    result_name: str,
    *,
    stats: _Stats,
    saved: int,
    started_at: datetime,
) -> IngestionRunResult:
    source = SourceIngestionResult(
        source_id=result_id,
        source_name=result_name,
        success=not stats.errors,
        articles_fetched=stats.work_done,
        articles_saved=saved,
        error="; ".join(stats.errors[:5]) if stats.errors else None,
    )
    logger.info(
        "[indiacourts] %s: pdfs=%d matches=%d saved=%d pending+=%d errors=%d "
        "parked_issues=%d non_english=%d",
        result_id,
        stats.pdfs_attempted,
        stats.matches,
        saved,
        stats.pending_added,
        len(stats.errors),
        len(stats.doc_issues),
        stats.non_english,
    )
    if stats.doc_issues:
        logger.info(
            "[indiacourts] %s: %d per-document issue(s) parked for retry, e.g.: %s",
            result_id,
            len(stats.doc_issues),
            "; ".join(stats.doc_issues[:3]),
        )
    return IngestionRunResult(
        started_at=started_at,
        finished_at=datetime.now(UTC),
        sources=[source],
        total_articles_saved=saved,
    )


def _save(store: ArticleStore, batch: list[RawArticle]) -> int:
    return store.save(batch) if batch else 0


def merge_sweep_spool(store: ArticleStore, spool_dir: str | Path) -> int:
    """Fold bulk-sweep chunk files into the raw store; retire what merged.

    The bulk sweep (indiacourts_bulk) NEVER writes the main store — this is
    the single writer, called at the start of the nightly forward ingest.
    Idempotent: the store link-dedupes, so re-merging a chunk (e.g. after a
    crash between save and retire, or a chunk re-synced from the bucket
    spool prefix) is harmless. Merged chunks move to ``ingested/`` for
    local tidiness; a bad line never sinks the merge.
    """
    spool = Path(spool_dir)
    if not spool.is_dir():
        return 0
    merged = 0
    done_dir = spool / "ingested"
    for chunk in sorted(spool.glob("*.jsonl")):
        batch: list[RawArticle] = []
        for line in chunk.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                batch.append(RawArticle.model_validate_json(line))
            except ValueError as exc:
                logger.warning("[india-sweep] bad spool line in %s skipped: %s", chunk.name, exc)
        saved = _save(store, batch)
        merged += saved
        done_dir.mkdir(exist_ok=True)
        os.replace(chunk, done_dir / chunk.name)
        logger.info(
            "[india-sweep] merged chunk %s: %d/%d new row(s)", chunk.name, saved, len(batch)
        )
    return merged


# ---------------------------------------------------------------------------
# Jobs


def resolve_ocr_backend(settings) -> OcrBackend | None:
    command = (settings.indiacourts_ocr_command or "").strip()
    if not command:
        return None
    try:
        return command_ocr_backend(command)
    except Exception as exc:  # noqa: BLE001 — a bad command is config, not fatal
        logger.warning("INDIACOURTS_OCR_COMMAND unusable (%s) — OCR disabled this run", exc)
        return None


def run_indiacourts_ingestion(
    *,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    state_dir: str = DEFAULT_INDIACOURTS_STATE_DIR,
    client: httpx.Client | None = None,
    ocr: OcrBackend | None = None,
    now: datetime | None = None,
) -> IngestionRunResult:
    """Forward ingest: scan judgments newly added to current-era partitions.

    The dataset regenerates daily; a partition whose parquet ETag is unchanged
    since the last completed pass costs one listing row and nothing else.
    """
    settings = get_settings()
    if not settings.indiacourts_enabled:
        return IngestionRunResult(
            started_at=now or datetime.now(UTC), sources=[], total_articles_saved=0
        )
    now = now or datetime.now(UTC)
    stats = _Stats()
    budget = _Budget(settings.indiacourts_max_pdfs_per_run)
    order, scope = _scoped_court_order(settings)
    sdir = Path(state_dir)
    pending = PendingQueue(sdir)
    article_store: ArticleStore = store or JsonlArticleStore(store_path)
    ocr_backend = ocr if ocr is not None else resolve_ocr_backend(settings)
    merged = merge_sweep_spool(article_store, settings.indiacourts_sweep_spool_dir)
    if merged:
        logger.info("[india-sweep] spool merge: %d bulk-sweep row(s) entered the store", merged)
    own_client = client is None
    http = client or httpx.Client(timeout=60.0)
    batch: list[RawArticle] = []
    try:
        for year in (now.year, now.year - 1):
            if budget.exhausted:
                break
            try:
                partitions = list_partitions(
                    http, base_url=settings.indiacourts_base_url, year=year
                )
            except IndiaCourtsError as exc:
                stats.errors.append(str(exc))
                continue
            for partition in _sort_partitions(partitions, order, scope):
                if budget.exhausted:
                    break
                stats.partitions_checked += 1
                _process_partition(
                    partition,
                    client=http,
                    settings=settings,
                    state_dir=sdir,
                    ocr=ocr_backend,
                    pending=pending,
                    budget=budget,
                    stats=stats,
                    store_batch=batch,
                    now=now,
                )
    finally:
        if own_client:
            http.close()
    saved = _save(article_store, batch) + merged
    return _finish(SOURCE_ID, SOURCE_NAME, stats=stats, saved=saved, started_at=now)


def _floor_year(settings) -> int | None:
    raw = (settings.indiacourts_history_floor or "").strip()
    if not raw:
        return None
    try:
        return int(raw.split("-")[0])
    except ValueError:
        logger.warning("Unparseable INDIACOURTS_HISTORY_FLOOR %r — sweep disabled", raw)
        return None


def run_indiacourts_history_sweep(
    *,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    state_path: str = DEFAULT_STATE_PATH,
    state_dir: str = DEFAULT_INDIACOURTS_STATE_DIR,
    client: httpx.Client | None = None,
    ocr: OcrBackend | None = None,
    now: datetime | None = None,
) -> IngestionRunResult:
    """Backward history walk: one bounded slice per run, newest year first.

    The cursor (a year) advances only when EVERY in-scope partition of that
    year is complete — a capped or failed run resumes the same year via the
    per-partition done-sets, so nothing is skipped. Stops at the floor year.
    """
    settings = get_settings()
    if not settings.indiacourts_enabled:
        return IngestionRunResult(
            started_at=now or datetime.now(UTC), sources=[], total_articles_saved=0
        )
    floor = _floor_year(settings)
    if floor is None or settings.indiacourts_history_max_pdfs_per_run <= 0:
        return IngestionRunResult(
            started_at=now or datetime.now(UTC), sources=[], total_articles_saved=0
        )

    now = now or datetime.now(UTC)
    ingest_state = JsonIngestState(state_path)
    # Forward ingest owns the current + previous year; history starts below.
    start_year = now.year - 2
    raw_cursor = ingest_state.get(_HISTORY_CURSOR_KEY)
    try:
        cursor = int(raw_cursor) if raw_cursor else start_year
    except ValueError:
        cursor = start_year
    cursor = min(cursor, start_year)
    if cursor < floor:
        return IngestionRunResult(
            started_at=now or datetime.now(UTC), sources=[], total_articles_saved=0
        )

    stats = _Stats()
    budget = _Budget(settings.indiacourts_history_max_pdfs_per_run)
    order, scope = _scoped_court_order(settings)
    sdir = Path(state_dir)
    pending = PendingQueue(sdir)
    article_store: ArticleStore = store or JsonlArticleStore(store_path)
    own_client = client is None
    http = client or httpx.Client(timeout=60.0)
    batch: list[RawArticle] = []
    ocr_backend = ocr if ocr is not None else resolve_ocr_backend(settings)
    try:
        while cursor >= floor and not budget.exhausted:
            try:
                partitions = _sort_partitions(
                    list_partitions(http, base_url=settings.indiacourts_base_url, year=cursor),
                    order,
                    scope,
                )
            except IndiaCourtsError as exc:
                stats.errors.append(str(exc))
                break  # listing failure: hold the cursor, retry next run
            year_complete = True
            for partition in partitions:
                if budget.exhausted:
                    year_complete = False
                    break
                stats.partitions_checked += 1
                done = _process_partition(
                    partition,
                    client=http,
                    settings=settings,
                    state_dir=sdir,
                    ocr=ocr_backend,
                    pending=pending,
                    budget=budget,
                    stats=stats,
                    store_batch=batch,
                    now=now,
                )
                if not done:
                    year_complete = False
            if not year_complete:
                break
            cursor -= 1
            ingest_state.set(_HISTORY_CURSOR_KEY, str(cursor))
    finally:
        if own_client:
            http.close()
    saved = _save(article_store, batch)
    return _finish(
        "indiacourts-history", f"{SOURCE_NAME} (history)", stats=stats, saved=saved, started_at=now
    )


def run_indiacourts_extract_pending(
    *,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    state_dir: str = DEFAULT_INDIACOURTS_STATE_DIR,
    client: httpx.Client | None = None,
    ocr: OcrBackend | None = None,
    now: datetime | None = None,
) -> IngestionRunResult:
    """Retry parked PDFs (failed fetch/parse, or awaiting OCR) after cool-down."""
    settings = get_settings()
    if not settings.indiacourts_enabled:
        return IngestionRunResult(
            started_at=now or datetime.now(UTC), sources=[], total_articles_saved=0
        )
    now = now or datetime.now(UTC)
    stats = _Stats()
    budget = _Budget(settings.indiacourts_extract_max_per_run)
    sdir = Path(state_dir)
    pending = PendingQueue(sdir)
    article_store: ArticleStore = store or JsonlArticleStore(store_path)
    ocr_backend = ocr if ocr is not None else resolve_ocr_backend(settings)
    own_client = client is None
    http = client or httpx.Client(timeout=60.0)
    batch: list[RawArticle] = []
    due = pending.due(now=now, retry_days=settings.indiacourts_retry_days)
    if not due:
        # Nothing to retry: emit NO result row. A success/0 row would classify
        # as "empty" and trip the broken-lane chip for a healthy idle queue.
        if own_client:
            http.close()
        return IngestionRunResult(started_at=now, sources=[], total_articles_saved=0)
    try:
        for pdf_key, entry in due:
            attempts = int(entry.get("attempts") or 0)
            if attempts >= _MAX_PENDING_ATTEMPTS:
                # Dead-letter: a PDF that failed this many spaced retries
                # (oversized, corrupt, gone) will not heal; stop burning
                # budget and bandwidth on it. Marking the basename done keeps
                # partition passes from re-discovering it.
                meta = meta_from_pending(pdf_key, entry)
                pending.remove(pdf_key)
                state = PartitionState(sdir, meta.partition)
                state.done.add(meta.pdf_basename)
                state.save()
                logger.warning(
                    "[indiacourts] giving up on %s after %d attempts (%s)",
                    pdf_key,
                    attempts,
                    entry.get("reason") or "?",
                )
                continue
            if not budget.take():
                break
            meta = meta_from_pending(pdf_key, entry)
            article, extracted = _process_meta(
                meta,
                client=http,
                settings=settings,
                ocr=ocr_backend,
                pending=pending,  # re-parks on repeat failure (attempts += 1)
                stats=stats,
                now=now,
            )
            if article is not None:
                batch.append(article)
            if extracted:
                # Text obtained (matched or not) — the entry is retired for
                # good and the partition remembers the basename as done.
                pending.remove(pdf_key)
                state = PartitionState(sdir, meta.partition)
                state.done.add(meta.pdf_basename)
                state.save()
    finally:
        if own_client:
            http.close()
    saved = _save(article_store, batch)
    return _finish(
        "indiacourts-extract", f"{SOURCE_NAME} (extract)", stats=stats, saved=saved, started_at=now
    )
