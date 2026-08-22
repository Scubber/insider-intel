"""Indian High Court judgments — client for the CC BY 4.0 eCourts open dataset.

Source: the AWS Open Data Registry dataset maintained by the
``vanga/indian-high-court-judgments`` project (25 High Courts, updated daily,
AWS-sponsored egress; companion Supreme Court dataset on the same registry).
License: CC BY 4.0 — full text may be stored and re-served with a credit line.

There is no search service: this lane downloads targeted partitions and runs
the insider lexicon LOCALLY. Bucket layout (verified live 2026-08-22):

    metadata/parquet/year=<Y>/court=<C>/bench=<B>/metadata.parquet
    data/pdf/year=<Y>/court=<C>/bench=<B>/<CNR>_<order>_<date>.pdf
    data/tar/year=<Y>/court=<C>/bench=<B>/data.tar   (bulk alt; unused here)

Court codes appear as ``27~1`` in metadata but ``27_1`` in object paths.
Unlike the CourtListener lane there are NO metadata-only stub rows: matching
requires the text anyway, so a judgment enters the corpus only after its PDF
text matched the lexicon — with that text already attached. PDFs that fail
extraction wait in a pending queue (see indiacourts_pipeline).
"""

from __future__ import annotations

import io
import logging
import re
import shlex
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from shared.schemas.articles import RawArticle

logger = logging.getLogger(__name__)

SOURCE_ID = "indiacourts-judgments"
SOURCE_NAME = "Indian High Court Judgments (eCourts open dataset)"

DEFAULT_BASE_URL = "https://indian-high-court-judgments.s3.amazonaws.com"

# Written at the head of RawArticle.content ("scored but hidden"); the spend
# gate strips lines with this prefix before any body-signal decision — keep in
# sync with shared/agents/summarize.py::_MATCH_MARKER_PREFIXES.
MATCH_MARKER_PREFIX = "IndiaCourts match: "

_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_WS_RE = re.compile(r"[ \t]+")
_PDF_NAME_RE = re.compile(r"^(?P<cnr>[A-Z0-9]+)_(?P<order>\d+)_(?P<date>[\d-]+)\.pdf$")
_OMISSION_MARKER = "\n…[middle of judgment omitted]…\n"

# Hand-authored insider-behavior patterns: each entry is a set of lowercase
# substrings that must ALL appear in the judgment text (compound AND — the
# Indian Kanoon-era query pack, projected onto a local scan). Single-term
# entries are reserved for terms that are unambiguous on their own. Broad
# standalone terms ("employee", "fraud", "confidential") stay out on purpose:
# coverage equals this list, and precision beats recall at ingest time.
INSIDER_PATTERNS: tuple[tuple[str, ...], ...] = (
    # Departing-employee data theft
    ("former employee", "confidential"),
    ("former employee", "trade secret"),
    ("former employee", "source code"),
    ("former employee", "customer data"),
    ("former employee", "database"),
    ("ex-employee", "confidential"),
    ("notice period", "confidential"),
    ("notice period", "data"),
    ("resignation", "copied", "confidential"),
    # Removable media / personal channels
    ("employee", "pen drive", "confidential"),
    ("employee", "pendrive", "confidential"),
    ("employee", "usb drive", "confidential"),
    ("personal email", "confidential", "employee"),
    ("personal e-mail", "confidential", "employee"),
    ("whatsapp", "confidential", "employee"),
    ("google drive", "confidential", "employee"),
    # Moonlighting / concurrent employment
    ("moonlighting",),
    ("dual employment", "employer"),
    ("concurrent employment", "employer"),
    # Destruction / concealment
    ("deleted", "emails", "confidential"),
    ("factory reset", "employee"),
    # Statutes and doctrine that mark employee data-misuse litigation
    ("criminal breach of trust", "employee"),
    ("breach of confidence", "employee"),
    ("technical know-how", "former employee"),
    ("section 43", "information technology act", "employee"),
    ("section 66", "information technology act", "employee"),
    ("misappropriation", "trade secret", "employee"),
)


class IndiaCourtsError(RuntimeError):
    """Dataset request or parse failure."""


@dataclass(frozen=True)
class PartitionRef:
    """One year/court/bench metadata partition (path-form court code)."""

    year: int
    court: str  # path form, e.g. "27_1"
    bench: str
    etag: str = ""

    @property
    def metadata_key(self) -> str:
        return (
            f"metadata/parquet/year={self.year}/court={self.court}"
            f"/bench={self.bench}/metadata.parquet"
        )

    @property
    def state_name(self) -> str:
        return f"{self.year}_{self.court}_{self.bench}"


@dataclass
class JudgmentMeta:
    """One judgment row from a partition's parquet metadata."""

    partition: PartitionRef
    title: str
    cnr: str
    pdf_link: str
    court_name: str = ""
    judge: str = ""
    description: str = ""
    disposal_nature: str = ""
    decision_date: datetime | None = None
    pdf_exists: bool | None = None

    @property
    def pdf_basename(self) -> str:
        return (self.pdf_link or "").rsplit("/", 1)[-1]

    @property
    def order_number(self) -> str | None:
        m = _PDF_NAME_RE.match(self.pdf_basename)
        return m.group("order") if m else None

    @property
    def pdf_key(self) -> str:
        p = self.partition
        return f"data/pdf/year={p.year}/court={p.court}/bench={p.bench}/{self.pdf_basename}"

    @property
    def dedupe_key(self) -> tuple[str, str, str]:
        """(cnr, decision_date, order_number) — the dataset's cross-source identity."""
        day = self.decision_date.date().isoformat() if self.decision_date else ""
        return (self.cnr or self.pdf_basename, day, self.order_number or "")


def court_path(code: str) -> str:
    """Metadata court code ('27~1') → object-path form ('27_1')."""
    return (code or "").strip().replace("~", "_")


def _object_url(base_url: str, key: str) -> str:
    return f"{base_url.rstrip('/')}/{quote(key)}"


def fetch_bytes(
    client: httpx.Client,
    key: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    max_bytes: int,
) -> bytes:
    url = _object_url(base_url, key)
    try:
        resp = client.get(url)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise IndiaCourtsError(f"HTTP {exc.response.status_code} for {key}") from exc
    except httpx.RequestError as exc:
        raise IndiaCourtsError(f"request failed for {key}: {exc}") from exc
    if len(resp.content) > max_bytes:
        raise IndiaCourtsError(f"{key} exceeds {max_bytes} bytes")
    return resp.content


def list_partitions(
    client: httpx.Client,
    *,
    base_url: str = DEFAULT_BASE_URL,
    year: int,
) -> list[PartitionRef]:
    """Enumerate one year's metadata partitions via public ListObjectsV2.

    Returns every court/bench partition with its current ETag (the change
    signal the pipeline diffs against stored state).
    """
    refs: list[PartitionRef] = []
    token: str | None = None
    prefix = f"metadata/parquet/year={year}/"
    while True:
        params = {"list-type": "2", "max-keys": "1000", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        try:
            resp = client.get(f"{base_url.rstrip('/')}/", params=params)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
        except httpx.HTTPError as exc:
            raise IndiaCourtsError(f"partition listing failed for {year}: {exc}") from exc
        except ET.ParseError as exc:
            raise IndiaCourtsError(f"partition listing XML malformed for {year}: {exc}") from exc
        for contents in root.findall(f"{_S3_NS}Contents"):
            key = contents.findtext(f"{_S3_NS}Key") or ""
            etag = (contents.findtext(f"{_S3_NS}ETag") or "").strip('"')
            m = re.match(
                r"metadata/parquet/year=(\d+)/court=([^/]+)/bench=([^/]+)/metadata\.parquet$",
                key,
            )
            if m:
                refs.append(
                    PartitionRef(
                        year=int(m.group(1)), court=m.group(2), bench=m.group(3), etag=etag
                    )
                )
        if (root.findtext(f"{_S3_NS}IsTruncated") or "").lower() != "true":
            break
        token = root.findtext(f"{_S3_NS}NextContinuationToken")
        if not token:
            break
    return refs


def read_partition_metadata(data: bytes, partition: PartitionRef) -> list[JudgmentMeta]:
    """Parse a partition's parquet bytes into judgment rows (tolerant).

    ``raw_html`` (the bulky scrape residue) is never materialized.
    """
    import pyarrow.parquet as pq

    try:
        table = pq.read_table(
            io.BytesIO(data),
            columns=None,
        )
    except Exception as exc:  # noqa: BLE001 — a corrupt partition must not kill the run
        raise IndiaCourtsError(f"unreadable parquet for {partition.state_name}: {exc}") from exc

    names = set(table.schema.names)
    wanted = [
        c
        for c in (
            "title",
            "cnr",
            "pdf_link",
            "court",
            "judge",
            "description",
            "disposal_nature",
            "decision_date",
            "pdf_exists",
        )
        if c in names
    ]
    rows = table.select(wanted).to_pylist() if wanted else []
    metas: list[JudgmentMeta] = []
    for row in rows:
        pdf_link = str(row.get("pdf_link") or "").strip()
        if not pdf_link:
            continue
        decision = row.get("decision_date")
        if isinstance(decision, datetime):
            decision = decision if decision.tzinfo else decision.replace(tzinfo=UTC)
        else:
            decision = None
        metas.append(
            JudgmentMeta(
                partition=partition,
                title=str(row.get("title") or "").strip(),
                cnr=str(row.get("cnr") or "").strip(),
                pdf_link=pdf_link,
                court_name=str(row.get("court") or "").strip(),
                judge=str(row.get("judge") or "").strip(),
                description=str(row.get("description") or "").strip(),
                disposal_nature=str(row.get("disposal_nature") or "").strip(),
                decision_date=decision,
                pdf_exists=(
                    row.get("pdf_exists") if isinstance(row.get("pdf_exists"), bool) else None
                ),
            )
        )
    return metas


def truncate_head_tail(text: str, max_chars: int) -> str:
    """Bounded storage cap keeping head AND tail with an explicit marker.

    Indian judgments put factual allegations near the top and the disposition
    at the end; blind head-only truncation loses the outcome. Ratio mirrors
    pack_case_text (5/6 head, 1/6 tail). Deterministic; unit-tested.
    """
    body = text or ""
    cap = max(500, max_chars)
    if len(body) <= cap:
        return body
    budget = cap - len(_OMISSION_MARKER)
    tail = budget // 6
    head = budget - tail
    return body[:head] + _OMISSION_MARKER + body[-tail:]


def pdf_bytes_to_text(data: bytes, *, max_chars: int) -> str:
    """Judgment PDF → plain text via the repo's existing pypdf dependency.

    Pages join with blank lines (paragraph structure preserved for the
    story/scan layers, unlike publication_extract's single-line collapse);
    over-cap documents keep head+tail via :func:`truncate_head_tail`.
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 — malformed PDFs route to pending
        raise IndiaCourtsError(f"pdf parse failed: {exc}") from exc
    joined = "\n\n".join(p for p in pages if p)
    cleaned = "\n".join(_WS_RE.sub(" ", line).strip() for line in joined.splitlines())
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return truncate_head_tail(cleaned, max_chars)


def scan_insider_patterns(text: str) -> list[str]:
    """Labels of INSIDER_PATTERNS whose terms ALL appear in the text."""
    lowered = (text or "").lower()
    if not lowered.strip():
        return []
    return [
        " + ".join(pattern)
        for pattern in INSIDER_PATTERNS
        if all(term in lowered for term in pattern)
    ]


def judgment_to_raw_article(
    meta: JudgmentMeta,
    matched: list[str],
    text: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
) -> RawArticle:
    """Map a lexicon-matched judgment to a RawArticle, full text attached.

    ``summary`` carries the human-visible court metadata and MUST emit the
    literal ``Court:`` / ``Docket:`` lines story clustering parses (CNR is the
    docket identity — punctuation-significant). The match marker + judgment
    text go in ``content`` (scored, never displayed); the spend gate strips
    the marker line before its body checks.
    """
    parts: list[str] = []
    if meta.court_name:
        parts.append(f"Court: {meta.court_name}")
    if meta.cnr:
        parts.append(f"Docket: {meta.cnr}")
    if meta.judge:
        parts.append(f"Judge: {meta.judge}")
    if meta.disposal_nature:
        parts.append(f"Disposal: {meta.disposal_nature}")
    marker = MATCH_MARKER_PREFIX + "; ".join(matched)
    return RawArticle(
        title=meta.title or meta.cnr or meta.pdf_basename,
        link=_object_url(base_url, meta.pdf_key),
        published=meta.decision_date,
        summary="\n".join(parts) if parts else None,
        content=f"{marker}\n{text}",
        source_id=SOURCE_ID,
        source_name=SOURCE_NAME,
        channel="filings",
        raw={
            "cnr": meta.cnr,
            "order_number": meta.order_number,
            "decision_date": meta.decision_date.isoformat() if meta.decision_date else None,
            "court_code": meta.partition.court,
            "bench": meta.partition.bench,
            "year": meta.partition.year,
            "pdf_key": meta.pdf_key,
            "matched_patterns": matched,
            "dataset": "indian-high-court-judgments (CC BY 4.0)",
        },
    )


def command_ocr_backend(command: str) -> Callable[[bytes], str]:
    """OCR backend running ``<command> <pdf-path>`` → text on stdout.

    Lets the Spark tenant plug in whatever tool wins the bench (an olmOCR
    wrapper, ocrmypdf+pdftotext, …) without a code change. Never raises out
    of the returned callable with the PDF contents in the message.
    """
    argv = shlex.split(command)

    def run(data: bytes) -> str:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(data)
            path = tmp.name
        try:
            proc = subprocess.run(
                [*argv, path],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if proc.returncode != 0:
                raise IndiaCourtsError(
                    f"ocr command exited {proc.returncode}: {proc.stderr.strip()[:200]}"
                )
            return proc.stdout
        except subprocess.TimeoutExpired as exc:
            raise IndiaCourtsError("ocr command timed out") from exc
        finally:
            Path(path).unlink(missing_ok=True)

    return run


def pending_entry(meta: JudgmentMeta, reason: str) -> dict[str, Any]:
    """Serializable pending-queue payload (enough to retry without the parquet)."""
    return {
        "reason": reason,
        "title": meta.title,
        "cnr": meta.cnr,
        "pdf_link": meta.pdf_link,
        "court_name": meta.court_name,
        "judge": meta.judge,
        "disposal_nature": meta.disposal_nature,
        "decision_date": meta.decision_date.isoformat() if meta.decision_date else None,
        "year": meta.partition.year,
        "court": meta.partition.court,
        "bench": meta.partition.bench,
    }


def meta_from_pending(key: str, entry: dict[str, Any]) -> JudgmentMeta:
    """Rebuild a JudgmentMeta from a pending-queue entry."""
    decision = None
    if entry.get("decision_date"):
        try:
            decision = datetime.fromisoformat(str(entry["decision_date"]))
            if decision.tzinfo is None:
                decision = decision.replace(tzinfo=UTC)
        except ValueError:
            decision = None
    partition = PartitionRef(
        year=int(entry.get("year") or 0),
        court=str(entry.get("court") or ""),
        bench=str(entry.get("bench") or ""),
    )
    return JudgmentMeta(
        partition=partition,
        title=str(entry.get("title") or ""),
        cnr=str(entry.get("cnr") or ""),
        pdf_link=str(entry.get("pdf_link") or key.rsplit("/", 1)[-1]),
        court_name=str(entry.get("court_name") or ""),
        judge=str(entry.get("judge") or ""),
        disposal_nature=str(entry.get("disposal_nature") or ""),
        decision_date=decision,
    )
