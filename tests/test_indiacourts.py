"""IndiaCourts lane: dataset client, lexicon scan, pipeline jobs, lane health.

All fixtures are synthetic (hand-built minimal PDFs, in-test parquet) served
through httpx.MockTransport — no live network, no copyrighted judgments.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from apps.aggregator.indiacourts import (
    MATCH_MARKER_PREFIX,
    SOURCE_ID,
    IndiaCourtsError,
    JudgmentMeta,
    PartitionRef,
    command_ocr_backend,
    court_path,
    judgment_to_raw_article,
    list_partitions,
    pdf_bytes_to_text,
    read_partition_metadata,
    scan_insider_patterns,
    truncate_head_tail,
)
from apps.aggregator.indiacourts_pipeline import (
    PendingQueue,
    run_indiacourts_extract_pending,
    run_indiacourts_history_sweep,
    run_indiacourts_ingestion,
)
from apps.aggregator.storage import JsonlArticleStore

# ---------------------------------------------------------------------------
# Synthetic fixtures


def _make_pdf(text: str) -> bytes:
    """Minimal one-page PDF whose text layer pypdf can extract."""
    safe = (
        text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)").replace("\n", " ")
    )
    stream = f"BT /F1 11 Tf 50 750 Td ({safe}) Tj ET".encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref_pos)
    )
    return bytes(out)


INSIDER_TEXT = (
    "IN THE HIGH COURT OF EXAMPLE. The complainant states that the former "
    "employee, before resignation, copied confidential design drawings to a "
    "pen drive and forwarded files to a personal email account. "
) + ("The matter was heard at length. " * 80)

BENIGN_TEXT = (
    "IN THE HIGH COURT OF EXAMPLE. This appeal concerns a boundary dispute "
    "between two agricultural land owners regarding survey numbers. "
) + ("Arguments were heard on the record. " * 80)


def _partition_parquet(rows: list[dict]) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {
            "court_code": [r.get("court_code", "27~1") for r in rows],
            "title": [r.get("title", "CASE/1/2026 of A Vs B") for r in rows],
            "cnr": [r.get("cnr", "HCBM01") for r in rows],
            "pdf_link": [r.get("pdf_link", "") for r in rows],
            "court": [r.get("court", "Bombay High Court") for r in rows],
            "judge": [r.get("judge", "") for r in rows],
            "description": [r.get("description", "") for r in rows],
            "disposal_nature": [r.get("disposal_nature", "") for r in rows],
            "decision_date": pa.array(
                [r.get("decision_date") for r in rows], type=pa.timestamp("us")
            ),
            "pdf_exists": [r.get("pdf_exists", True) for r in rows],
            "raw_html": [r.get("raw_html", "<button>...</button>") for r in rows],
        }
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


class FakeBucket:
    """In-memory dataset bucket served through httpx.MockTransport."""

    def __init__(self) -> None:
        # (year, court, bench) -> (etag, rows)
        self.partitions: dict[tuple[int, str, str], tuple[str, list[dict]]] = {}
        # pdf basename -> bytes | "missing"
        self.pdfs: dict[str, bytes | str] = {}
        self.requests: list[str] = []
        self.list_page_size = 1000

    def add_judgment(
        self,
        *,
        year: int = 2026,
        court: str = "27_1",
        bench: str = "benchx",
        cnr: str,
        text: str | bytes | None,
        etag: str = "etag-1",
        decision: datetime | None = None,
        title: str | None = None,
    ) -> str:
        basename = f"{cnr}_1_2026-01-05.pdf"
        key = (year, court, bench)
        _etag, rows = self.partitions.get(key, (etag, []))
        rows = list(rows)
        rows.append(
            {
                "cnr": cnr,
                "title": title or f"{cnr} of State Vs Accused",
                "pdf_link": f"court/orders/{basename}",
                "decision_date": decision or datetime(year, 1, 5),
            }
        )
        self.partitions[key] = (etag, rows)
        if text is None:
            self.pdfs[basename] = "missing"
        elif isinstance(text, bytes):
            self.pdfs[basename] = text
        else:
            self.pdfs[basename] = _make_pdf(text)
        return basename

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.lstrip("/")
        self.requests.append(path or f"LIST:{request.url.params.get('prefix', '')}")
        if not path:  # ListObjectsV2
            prefix = request.url.params.get("prefix", "")
            token = request.url.params.get("continuation-token")
            keys = sorted(
                f"metadata/parquet/year={y}/court={c}/bench={b}/metadata.parquet"
                for (y, c, b) in self.partitions
            )
            keys = [k for k in keys if k.startswith(prefix)]
            start = int(token) if token else 0
            page = keys[start : start + self.list_page_size]
            truncated = start + self.list_page_size < len(keys)
            contents = "".join(
                f"<Contents><Key>{k}</Key><ETag>&quot;{self._etag_for(k)}&quot;</ETag></Contents>"
                for k in page
            )
            nxt = (
                f"<NextContinuationToken>{start + self.list_page_size}</NextContinuationToken>"
                if truncated
                else ""
            )
            xml = (
                '<?xml version="1.0"?>'
                '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                f"<IsTruncated>{str(truncated).lower()}</IsTruncated>{nxt}{contents}"
                "</ListBucketResult>"
            )
            return httpx.Response(200, text=xml)
        if path.startswith("metadata/parquet/"):
            for (y, c, b), (_etag, rows) in self.partitions.items():
                if path == f"metadata/parquet/year={y}/court={c}/bench={b}/metadata.parquet":
                    return httpx.Response(200, content=_partition_parquet(rows))
            return httpx.Response(404)
        if path.startswith("data/pdf/"):
            basename = path.rsplit("/", 1)[-1]
            blob = self.pdfs.get(basename)
            if blob is None or blob == "missing":
                return httpx.Response(404)
            assert isinstance(blob, bytes)
            return httpx.Response(200, content=blob)
        return httpx.Response(404)

    def _etag_for(self, key: str) -> str:
        for (y, c, b), (etag, _rows) in self.partitions.items():
            if key == f"metadata/parquet/year={y}/court={c}/bench={b}/metadata.parquet":
                return etag
        return "unknown"

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def pdf_fetches(self) -> int:
        return sum(1 for p in self.requests if p.startswith("data/pdf/"))


def _enable(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    monkeypatch.setenv("INDIACOURTS_ENABLED", "true")
    monkeypatch.setenv("INDIACOURTS_REQUEST_DELAY_SECONDS", "0")
    for key, value in extra.items():
        monkeypatch.setenv(key, value)


def _run_forward(bucket: FakeBucket, tmp_path: Path, **kwargs):
    with bucket.client() as client:
        return run_indiacourts_ingestion(
            store_path=str(tmp_path / "raw.jsonl"),
            state_dir=str(tmp_path / "state"),
            client=client,
            now=datetime(2026, 8, 22, tzinfo=UTC),
            **kwargs,
        )


# ---------------------------------------------------------------------------
# Client units


def test_court_path_maps_metadata_codes_to_object_paths() -> None:
    assert court_path("27~1") == "27_1"
    assert court_path(" 7~26 ") == "7_26"
    assert court_path("3_22") == "3_22"


def test_list_partitions_parses_and_paginates() -> None:
    bucket = FakeBucket()
    bucket.add_judgment(cnr="A1", text=BENIGN_TEXT, year=2026, court="27_1", bench="b1")
    bucket.add_judgment(cnr="A2", text=BENIGN_TEXT, year=2026, court="7_26", bench="b2")
    bucket.list_page_size = 1  # force continuation-token paging
    with bucket.client() as client:
        refs = list_partitions(client, year=2026)
    assert {(r.court, r.bench) for r in refs} == {("27_1", "b1"), ("7_26", "b2")}
    assert all(r.etag == "etag-1" for r in refs)


def test_read_partition_metadata_is_tolerant() -> None:
    ref = PartitionRef(year=2026, court="27_1", bench="b1")
    rows = [
        {"cnr": "OK1", "pdf_link": "court/orders/OK1_1_2026-01-05.pdf"},
        {"cnr": "NOPDF", "pdf_link": ""},  # dropped: no pdf to fetch
    ]
    metas = read_partition_metadata(_partition_parquet(rows), ref)
    assert [m.cnr for m in metas] == ["OK1"]
    assert metas[0].decision_date is None or metas[0].decision_date.tzinfo is not None
    assert metas[0].pdf_key == "data/pdf/year=2026/court=27_1/bench=b1/OK1_1_2026-01-05.pdf"
    assert metas[0].order_number == "1"
    with pytest.raises(IndiaCourtsError):
        read_partition_metadata(b"not parquet at all", ref)


def test_truncate_head_tail_is_deterministic_with_marker() -> None:
    assert truncate_head_tail("short", 1_000) == "short"
    text = ("H" * 4_000) + ("M" * 4_000) + ("T" * 4_000)
    out = truncate_head_tail(text, 1_200)
    assert len(out) <= 1_200
    assert "…[middle of judgment omitted]…" in out
    assert out.startswith("H")
    assert out.endswith("T")  # the disposition end survives
    # Deterministic: same input, same output.
    assert out == truncate_head_tail(text, 1_200)


def test_pdf_bytes_to_text_extracts_and_rejects_garbage() -> None:
    text = pdf_bytes_to_text(_make_pdf("The former employee copied files."), max_chars=10_000)
    assert "former employee copied files" in text
    with pytest.raises(IndiaCourtsError):
        pdf_bytes_to_text(b"\x00\x01 not a pdf", max_chars=10_000)


def test_scan_insider_patterns_compound_and_semantics() -> None:
    assert scan_insider_patterns(INSIDER_TEXT)  # former employee + confidential
    assert not scan_insider_patterns(BENIGN_TEXT)
    # Compound patterns need every term: "former employee" alone is not enough.
    assert not scan_insider_patterns("the former employee attended the hearing")
    # Single-term patterns fire alone only when unambiguous by construction.
    assert scan_insider_patterns("the respondent admitted to moonlighting for a rival firm")


def test_judgment_to_raw_article_contract() -> None:
    from shared.schemas.articles import resolve_channel
    from shared.utils.story_key import parse_filing_reference

    ref = PartitionRef(year=2026, court="27_1", bench="b1")
    meta = JudgmentMeta(
        partition=ref,
        title="CRL.A. 123/2026 of State Vs Person",
        cnr="HCBM020000062026",
        pdf_link="court/orders/HCBM020000062026_1_2026-01-05.pdf",
        court_name="Bombay High Court",
        judge="HON'BLE JUDGE",
        disposal_nature="DISMISSED",
        decision_date=datetime(2026, 1, 5, tzinfo=UTC),
    )
    article = judgment_to_raw_article(meta, ["former employee + confidential"], INSIDER_TEXT)
    # The judgment text is the hidden content; the match labels live ONLY in
    # raw — a content marker would hand the spend gate its own query terms
    # once clean_text flattens it (2026-08-22 review).
    assert article.content is not None
    assert MATCH_MARKER_PREFIX not in article.content
    assert INSIDER_TEXT[:50] in article.content
    assert article.raw is not None
    assert article.raw["matched_patterns"] == ["former employee + confidential"]
    assert article.summary is not None
    assert MATCH_MARKER_PREFIX not in article.summary
    # Story clustering parses the literal Court:/Docket: lines (CNR = docket).
    assert "Court: Bombay High Court" in article.summary
    assert "Docket: HCBM020000062026" in article.summary
    assert parse_filing_reference(article.summary) is not None
    # Filings channel end to end, including the spend gate's re-derivation.
    assert article.channel == "filings"
    assert resolve_channel(article.source_id) == "filings"
    assert article.link.endswith("HCBM020000062026_1_2026-01-05.pdf")
    assert article.raw is not None and article.raw["cnr"] == "HCBM020000062026"
    # Language stamped from the scanned text itself (dataset has no column).
    assert article.legal_metadata is not None and article.legal_metadata.language == "en"


def test_detect_language_scripts() -> None:
    from apps.aggregator.indiacourts import detect_language

    english = "the former employee copied confidential files onto a pen drive " * 10
    hindi = "अभियुक्त कर्मचारी ने गोपनीय दस्तावेज़ों की प्रतिलिपि बनाई और गबन किया " * 10
    mixed_header = ("IN THE HIGH COURT OF JUDICATURE " * 3) + hindi
    assert detect_language(english) == "en"
    assert detect_language(hindi) == "hi"
    # Hindi body under an English boilerplate header still stamps hi.
    assert detect_language(mixed_header) == "hi"
    assert detect_language("short") is None


def test_ocr_pdf_helper_runs_pdftoppm_then_tesseract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Tesseract helper: rasterize pages, OCR each, join stdout — verified
    with a faked subprocess so CI needs no OCR binaries."""
    import subprocess as sp

    from apps.aggregator import ocr_pdf as mod

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[0] == "pdftoppm":
            prefix = Path(argv[-1])
            for i in (1, 2):
                (prefix.parent / f"{prefix.name}-{i}.png").write_bytes(b"png")
            return sp.CompletedProcess(argv, 0, stdout=b"", stderr=b"")
        if argv[0] == "tesseract":
            page = Path(argv[1]).name
            return sp.CompletedProcess(argv, 0, stdout=f"text-of-{page}\n", stderr="")
        raise AssertionError(f"unexpected command {argv}")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    out = mod.ocr_pdf(str(pdf), max_pages=5)
    assert "text-of-page-1.png" in out and "text-of-page-2.png" in out
    assert calls[0][0] == "pdftoppm" and "-l" in calls[0]
    assert calls[0][calls[0].index("-l") + 1] == "5"  # page cap threaded through
    assert sum(1 for c in calls if c[0] == "tesseract") == 2


def test_match_marker_is_stripped_by_the_spend_gate() -> None:
    """The lane's marker prefix must stay in sync with the gate's strip list."""
    from shared.agents.summarize import strip_match_markers

    marked = f"{MATCH_MARKER_PREFIX}former employee + confidential\nreal body"
    assert strip_match_markers(marked) == "real body"


def test_command_ocr_backend_runs_and_fails_cleanly() -> None:
    ok = command_ocr_backend("sh -c 'echo OCRTEXT'")
    assert ok(b"%PDF-fake").strip() == "OCRTEXT"
    bad = command_ocr_backend("false")
    with pytest.raises(IndiaCourtsError):
        bad(b"%PDF-fake")


# ---------------------------------------------------------------------------
# Forward ingestion


def test_forward_ingestion_stores_only_matches_with_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(monkeypatch)
    bucket = FakeBucket()
    bucket.add_judgment(cnr="MATCH1", text=INSIDER_TEXT)
    bucket.add_judgment(cnr="BENIGN1", text=BENIGN_TEXT)
    result = _run_forward(bucket, tmp_path)
    assert result.total_articles_saved == 1
    assert result.sources[0].source_id == SOURCE_ID
    assert result.sources[0].success
    rows = JsonlArticleStore(str(tmp_path / "raw.jsonl")).load_all()
    assert len(rows) == 1 and rows[0].raw is not None and rows[0].raw["cnr"] == "MATCH1"
    assert "pen drive" in (rows[0].content or "")
    # Partition state recorded both basenames as done.
    state_files = list((tmp_path / "state").glob("2026_27_1_*.json"))
    assert len(state_files) == 1
    payload = json.loads(state_files[0].read_text())
    assert payload["complete"] and len(payload["done"]) == 2


def test_parked_pdf_issues_do_not_fail_the_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The 2026-08-23 live smoke: 5/200 truncated PDFs parked cleanly, yet the
    run reported [FAIL] — per-document problems ARE the pending queue's
    designed path and must never fail the lane (a real cycle almost always has
    some, so the old behavior meant a permanent false [LANE-BROKEN])."""
    _enable(monkeypatch)
    bucket = FakeBucket()
    bucket.add_judgment(cnr="MATCH1", text=INSIDER_TEXT)
    bucket.add_judgment(cnr="GONE1", text=None)  # 404s -> parks in pending
    bucket.add_judgment(cnr="TRUNC1", text=b"%PDF-1.4 truncated garbage")  # parse fails
    result = _run_forward(bucket, tmp_path)
    src = result.sources[0]
    assert src.success, src.error
    assert src.error is None
    assert result.total_articles_saved == 1
    assert len(PendingQueue(tmp_path / "state")) == 2


def test_corrupt_dataset_object_is_retired_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The upstream scraper sometimes stored an error body under the .pdf key
    (live find 2026-08-23: bytes starting '{"msg'). Those can never parse or
    OCR — retire them immediately instead of five spaced retries each."""
    _enable(monkeypatch)
    bucket = FakeBucket()
    bucket.add_judgment(cnr="CORRUPT1", text=b'{"msg": "scrape failed upstream"}')
    result = _run_forward(bucket, tmp_path)
    assert result.sources[0].success
    # Never parked — the basename is done for good.
    assert len(PendingQueue(tmp_path / "state")) == 0
    state_files = list((tmp_path / "state").glob("*.json"))
    payload = json.loads(state_files[0].read_text())
    assert any("CORRUPT1" in name for name in payload["done"])


def test_partition_listing_failure_fails_the_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Systemic failures (the lane cannot enumerate the dataset) still fail
    the run — only per-document issues are exempt."""
    _enable(monkeypatch)
    from apps.aggregator import indiacourts_pipeline as pipeline
    from apps.aggregator.indiacourts import IndiaCourtsError

    def boom(*args, **kwargs):
        raise IndiaCourtsError("listing exploded")

    monkeypatch.setattr(pipeline, "list_partitions", boom)
    bucket = FakeBucket()
    result = _run_forward(bucket, tmp_path)
    src = result.sources[0]
    assert not src.success
    assert "listing exploded" in (src.error or "")


def test_forward_second_run_skips_unchanged_partitions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(monkeypatch)
    bucket = FakeBucket()
    bucket.add_judgment(cnr="MATCH1", text=INSIDER_TEXT)
    _run_forward(bucket, tmp_path)
    first_fetches = bucket.pdf_fetches()
    second = _run_forward(bucket, tmp_path)
    # ETag unchanged → the second run fetched no parquet and no PDFs…
    assert bucket.pdf_fetches() == first_fetches
    # …but still reports the partitions it CHECKED, so an idle cycle (the
    # daily dataset hasn't regenerated since the last pass) classifies
    # "ok", never "empty" → never a spurious [LANE-BROKEN] chip.
    assert second.sources[0].success and second.sources[0].articles_fetched >= 1


def test_pending_dead_letter_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A permanently failing PDF stops consuming budget after 5 spaced tries."""
    _enable(monkeypatch)
    bucket = FakeBucket()
    bucket.add_judgment(cnr="GONE", text=None)  # 404s forever
    _run_forward(bucket, tmp_path)
    pending = PendingQueue(tmp_path / "state")
    assert len(pending) == 1
    key = next(iter(pending._items))
    pending._items[key]["attempts"] = 5
    pending.save()
    with bucket.client() as client:
        result = run_indiacourts_extract_pending(
            store_path=str(tmp_path / "raw.jsonl"),
            state_dir=str(tmp_path / "state"),
            client=client,
            now=datetime(2026, 9, 9, tzinfo=UTC),
        )
    assert len(PendingQueue(tmp_path / "state")) == 0  # dead-lettered
    assert result.sources[0].articles_fetched == 0 or result.sources == []


def test_forward_diff_processes_only_new_judgments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(monkeypatch)
    bucket = FakeBucket()
    bucket.add_judgment(cnr="OLD1", text=BENIGN_TEXT, etag="etag-1")
    _run_forward(bucket, tmp_path)
    # Daily regeneration: new judgment appended, etag changes.
    key = (2026, "27_1", "benchx")
    _etag, rows = bucket.partitions[key]
    bucket.partitions[key] = ("etag-2", rows)
    bucket.add_judgment(cnr="NEW1", text=INSIDER_TEXT, etag="etag-2")
    before = bucket.pdf_fetches()
    result = _run_forward(bucket, tmp_path)
    assert bucket.pdf_fetches() - before == 1  # only NEW1's pdf
    assert result.total_articles_saved == 1


def test_forward_cap_bounds_work_and_resumes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(monkeypatch, INDIACOURTS_MAX_PDFS_PER_RUN="1")
    bucket = FakeBucket()
    bucket.add_judgment(cnr="M1", text=INSIDER_TEXT)
    bucket.add_judgment(cnr="M2", text=INSIDER_TEXT)
    first = _run_forward(bucket, tmp_path)
    assert first.sources[0].articles_fetched == 1  # cap respected
    second = _run_forward(bucket, tmp_path)
    assert second.sources[0].articles_fetched == 1  # resumed the remainder
    rows = JsonlArticleStore(str(tmp_path / "raw.jsonl")).load_all()
    assert {r.raw["cnr"] for r in rows if r.raw} == {"M1", "M2"}


def test_disabled_lane_makes_no_requests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("INDIACOURTS_ENABLED", "false")
    bucket = FakeBucket()
    bucket.add_judgment(cnr="M1", text=INSIDER_TEXT)
    result = _run_forward(bucket, tmp_path)
    assert result.sources == [] and result.total_articles_saved == 0
    assert bucket.requests == []


def test_court_scope_and_priority_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(monkeypatch, INDIACOURTS_COURTS="7_26")
    bucket = FakeBucket()
    bucket.add_judgment(cnr="INSCOPE", text=INSIDER_TEXT, court="7_26", bench="delhi1")
    bucket.add_judgment(cnr="OUTSCOPE", text=INSIDER_TEXT, court="27_1", bench="b1")
    result = _run_forward(bucket, tmp_path)
    rows = JsonlArticleStore(str(tmp_path / "raw.jsonl")).load_all()
    assert {r.raw["cnr"] for r in rows if r.raw} == {"INSCOPE"}
    assert result.sources[0].articles_fetched == 1


# ---------------------------------------------------------------------------
# Pending queue: fetch failures and the OCR route


def test_fetch_failure_parks_and_retries_after_cooldown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(monkeypatch)
    bucket = FakeBucket()
    basename = bucket.add_judgment(cnr="GONE1", text=None)  # 404s
    _run_forward(bucket, tmp_path)
    pending = PendingQueue(tmp_path / "state")
    assert len(pending) == 1

    # PDF appears later; retry before the cool-down does nothing…
    bucket.pdfs[basename] = _make_pdf(INSIDER_TEXT)
    with bucket.client() as client:
        early = run_indiacourts_extract_pending(
            store_path=str(tmp_path / "raw.jsonl"),
            state_dir=str(tmp_path / "state"),
            client=client,
            now=datetime(2026, 8, 22, 1, tzinfo=UTC),
        )
    # Nothing was due → no result row at all (a success/0 row would read as an
    # "empty" failure and trip the broken-lane chip for a healthy idle queue).
    assert early.sources == []

    # …and succeeds after it, retiring the entry and storing the match.
    with bucket.client() as client:
        late = run_indiacourts_extract_pending(
            store_path=str(tmp_path / "raw.jsonl"),
            state_dir=str(tmp_path / "state"),
            client=client,
            now=datetime(2026, 8, 30, tzinfo=UTC),
        )
    assert late.total_articles_saved == 1
    assert len(PendingQueue(tmp_path / "state")) == 0


def test_scanned_pdf_routes_to_ocr_or_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(monkeypatch)
    bucket = FakeBucket()
    bucket.add_judgment(cnr="SCAN1", text="")  # empty text layer
    # Without a backend: parked with reason ocr.
    _run_forward(bucket, tmp_path)
    pending = PendingQueue(tmp_path / "state")
    assert len(pending) == 1
    key, entry = pending.due(now=datetime(2026, 9, 9, tzinfo=UTC), retry_days=7.0)[0]
    assert entry["reason"] == "ocr"

    # With a backend (fake OCR returning insider text): extracted and stored.
    with bucket.client() as client:
        result = run_indiacourts_extract_pending(
            store_path=str(tmp_path / "raw.jsonl"),
            state_dir=str(tmp_path / "state"),
            client=client,
            ocr=lambda data: INSIDER_TEXT,
            now=datetime(2026, 9, 9, tzinfo=UTC),
        )
    assert result.total_articles_saved == 1
    assert len(PendingQueue(tmp_path / "state")) == 0


# ---------------------------------------------------------------------------
# History sweep


def _run_history(bucket: FakeBucket, tmp_path: Path, *, now: datetime):
    with bucket.client() as client:
        return run_indiacourts_history_sweep(
            store_path=str(tmp_path / "raw.jsonl"),
            state_path=str(tmp_path / "ingest_state.json"),
            state_dir=str(tmp_path / "state"),
            client=client,
            now=now,
        )


def test_history_sweep_walks_back_and_respects_floor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(monkeypatch, INDIACOURTS_HISTORY_FLOOR="2023-01-01")
    now = datetime(2026, 8, 22, tzinfo=UTC)
    bucket = FakeBucket()
    bucket.add_judgment(cnr="H2024", text=INSIDER_TEXT, year=2024)
    bucket.add_judgment(cnr="H2023", text=INSIDER_TEXT, year=2023)
    result = _run_history(bucket, tmp_path, now=now)
    # Start year is now.year-2 (forward owns current+previous): 2024 → 2023.
    rows = JsonlArticleStore(str(tmp_path / "raw.jsonl")).load_all()
    assert {r.raw["cnr"] for r in rows if r.raw} == {"H2024", "H2023"}
    assert result.sources[0].source_id == "indiacourts-history"
    # Cursor ended below the floor → the next run is a no-op slice.
    again = _run_history(bucket, tmp_path, now=now)
    assert again.sources == []


def test_history_cursor_holds_until_year_completes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(
        monkeypatch,
        INDIACOURTS_HISTORY_FLOOR="2023-01-01",
        INDIACOURTS_HISTORY_MAX_PDFS_PER_RUN="1",
    )
    now = datetime(2026, 8, 22, tzinfo=UTC)
    bucket = FakeBucket()
    bucket.add_judgment(cnr="A2024", text=INSIDER_TEXT, year=2024)
    bucket.add_judgment(cnr="B2024", text=BENIGN_TEXT, year=2024)
    _run_history(bucket, tmp_path, now=now)
    state_file = tmp_path / "ingest_state.json"
    # Capped mid-year: the cursor must NOT have advanced past 2024 (the file
    # only appears on the first advance, so absence IS the held state).
    if state_file.exists():
        assert json.loads(state_file.read_text()).get("indiacourts_history:cursor") == "2024"
    _run_history(bucket, tmp_path, now=now)  # finishes 2024 → advances
    state = json.loads(state_file.read_text())
    assert state["indiacourts_history:cursor"] == "2023"


def test_history_disabled_without_floor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(monkeypatch, INDIACOURTS_HISTORY_FLOOR="")
    bucket = FakeBucket()
    result = _run_history(bucket, tmp_path, now=datetime(2026, 8, 22, tzinfo=UTC))
    assert result.sources == []
    assert bucket.requests == []


# ---------------------------------------------------------------------------
# Lane health


def test_lane_health_expected_only_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.aggregator.lane_health import _infer_kind, expected_lane_specs

    monkeypatch.setenv("INDIACOURTS_ENABLED", "false")
    ids = {lane.id for lane in expected_lane_specs(feeds=[])}
    assert SOURCE_ID not in ids  # disabled = cleanly absent, never "skipped"

    monkeypatch.setenv("INDIACOURTS_ENABLED", "true")
    lanes = {lane.id: lane for lane in expected_lane_specs(feeds=[])}
    assert SOURCE_ID in lanes and lanes[SOURCE_ID].kind == "court"

    # Dynamic result rows (history/extract) classify as court lanes too.
    assert _infer_kind("indiacourts-history") == "court"
    assert _infer_kind("indiacourts-extract") == "court"
