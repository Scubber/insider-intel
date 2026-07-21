"""Pydantic models for RSS ingestion and raw article storage."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl

from shared.schemas.discovery import CaseDiscovery
from shared.schemas.forensics import PerCaseForensics

# Provenance lane for stream filters (orthogonal to Insider Focus).
Channel = Literal["news", "filings", "tips", "social", "publications"]

# Insider disposition inferred per article (None = unclassified).
InsiderType = Literal["negligent", "malicious", "unintentional"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def resolve_channel(
    source_id: str,
    channel: str | None = None,
    *,
    category: str | None = None,
) -> Channel:
    """Resolve channel from explicit value, source id, or category."""
    sid = (source_id or "").strip().lower()
    cat = (category or "").strip().lower()
    # social- must win over the legacy reddit- -> tips rule below
    if sid.startswith("social-") or cat.startswith("social") or channel == "social":
        return "social"
    if sid.startswith("pub-") or channel == "publications" or cat == "publications":
        return "publications"
    if sid.startswith(("reddit-", "tip-")) or "tips-" in cat or cat == "tips":
        return "tips"
    if "courtlistener" in sid or cat in {"filings", "court", "recap"}:
        return "filings"
    if channel in ("news", "filings", "tips"):
        return channel  # type: ignore[return-value]
    return "news"


class FeedSource(BaseModel):
    """A configured RSS/Atom feed to ingest."""

    id: str = Field(..., description="Stable identifier used in storage and logs")
    name: str = Field(..., description="Human-readable source name")
    url: HttpUrl = Field(..., description="Feed URL")
    enabled: bool = True
    category: str | None = Field(
        default=None,
        description="Optional grouping label (e.g. 'news', 'advisory')",
    )
    channel: Channel = Field(
        default="news",
        description="Provenance lane: news | filings | tips (social/Reddit tip accounts)",
    )


class RawArticle(BaseModel):
    """Normalized article extracted from a feed entry.

    Kept intentionally simple so LangGraph processing agents can consume
    this as a stable input schema later.
    """

    title: str
    link: str
    published: datetime | None = None
    summary: str | None = None
    content: str | None = Field(
        default=None,
        description="Full plain-text body for scoring/search (not displayed)",
    )
    source_id: str
    source_name: str
    channel: Channel = Field(
        default="news",
        description="Provenance lane: news | filings | tips",
    )
    ingested_at: datetime = Field(default_factory=_utc_now)
    # Optional raw payload for debugging / future re-processing
    raw: dict[str, Any] | None = None


class SourceIngestionResult(BaseModel):
    """Outcome of ingesting a single feed source."""

    source_id: str
    source_name: str
    success: bool
    articles_fetched: int = 0
    articles_saved: int = 0
    error: str | None = None


class IngestionRunResult(BaseModel):
    """Outcome of a full multi-source ingestion run."""

    started_at: datetime
    finished_at: datetime | None = None
    sources: list[SourceIngestionResult] = Field(default_factory=list)
    total_articles_saved: int = 0

    @property
    def success_count(self) -> int:
        return sum(1 for s in self.sources if s.success)

    @property
    def failure_count(self) -> int:
        return sum(1 for s in self.sources if not s.success)


class ItmHit(BaseModel):
    """A matched Insider Threat Matrix™ technique or subsection."""

    id: str = Field(..., description="ITM technique id (e.g. IF001, ME005)")
    title: str
    theme: str = Field(..., description="motive|means|preparation|infringement|anti-forensics")
    article_id: str = Field(..., description="ITM article id AR1–AR5")
    matched_aliases: list[str] = Field(
        default_factory=list,
        description="Which aliases / title phrases matched in the article text",
    )
    source: Literal["lexical", "llm"] = Field(
        default="lexical",
        description="How the technique was mapped: alias match or LLM adjudication",
    )


class ControlRef(BaseModel):
    """ITM Detection (DT*) or Prevention (PV*) linked via matched techniques."""

    id: str = Field(..., description="Control id (e.g. DT050, PV024)")
    title: str


class ExtractedEntities(BaseModel):
    """Lightweight entities pulled from article text (MVP heuristics)."""

    cves: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    keywords_hit: list[str] = Field(
        default_factory=list,
        description="Match signals from ITM aliases, framing terms, and technique ids",
    )
    operator_terms: list[str] = Field(
        default_factory=list,
        description="Searchable terms for Teams/email/SIEM paste (not taxonomy ids)",
    )
    itm_hits: list[ItmHit] = Field(
        default_factory=list,
        description="Matched Insider Threat Matrix™ techniques",
    )
    related_detections: list[ControlRef] = Field(
        default_factory=list,
        description="ITM detections linked from matched techniques (SIEM handoff)",
    )
    related_preventions: list[ControlRef] = Field(
        default_factory=list,
        description="ITM preventions linked from matched techniques (control handoff)",
    )


_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

_CASE_FIELD_MAX_CHARS = 200
# DETECTED VIA / OUTCOME are full-sentence narrative fields (esp. on court
# filings), not short labels like actor_role — a 200-char clamp guillotined
# them mid-sentence in the UI. Give them their own generous limit.
_CASE_TEXT_MAX_CHARS = 800
_CASE_LIST_MAX_ITEMS = 8
_CASE_LIST_ITEM_MAX_CHARS = 120


def _clean_case_str(value: str | None, limit: int = _CASE_FIELD_MAX_CHARS) -> str | None:
    if value is None:
        return None
    cleaned = _CTRL_CHARS_RE.sub(" ", str(value)).replace("\n", " ").replace("\r", " ")
    cleaned = " ".join(cleaned.split()).strip()
    return cleaned[:limit] or None


def _clean_case_list(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_case_str(value, _CASE_LIST_ITEM_MAX_CHARS)
        if not cleaned or cleaned.lower() in seen:
            continue
        seen.add(cleaned.lower())
        out.append(cleaned)
        if len(out) >= _CASE_LIST_MAX_ITEMS:
            break
    return out


class CaseRecord(BaseModel):
    """Structured insider-case facts extracted by the ingest summarizer LLM.

    Fields render in the UI as plain text and feed hunt-term generation, so
    they must pass through ``sanitized()`` before storage.
    """

    is_insider_case: bool = False
    actor_role: str | None = Field(
        default=None, description="e.g. 'departing engineer', 'contractor sysadmin'"
    )
    access_vector: str | None = Field(
        default=None, description="e.g. 'privileged VPN access', 'source repo access'"
    )
    motive_signals: list[str] = Field(default_factory=list)
    methods: list[str] = Field(
        default_factory=list,
        description="Tools/techniques close to the article's own wording",
    )
    exfil_channels: list[str] = Field(
        default_factory=list, description="e.g. 'personal Gmail', 'USB drive'"
    )
    timeframe: str | None = None
    detection_trigger: str | None = Field(
        default=None, description="What surfaced the activity, if stated"
    )
    outcome: str | None = Field(
        default=None, description="Charges, termination, settlement, ongoing…"
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    extracted_at: datetime | None = None
    model: str | None = Field(default=None, description="LLM that produced the record")

    def sanitized(self) -> CaseRecord:
        """Clamp lengths and strip control chars — output is UI-rendered text."""
        return self.model_copy(
            update={
                "actor_role": _clean_case_str(self.actor_role),
                "access_vector": _clean_case_str(self.access_vector),
                "motive_signals": _clean_case_list(self.motive_signals),
                "methods": _clean_case_list(self.methods),
                "exfil_channels": _clean_case_list(self.exfil_channels),
                "timeframe": _clean_case_str(self.timeframe),
                "detection_trigger": _clean_case_str(self.detection_trigger, _CASE_TEXT_MAX_CHARS),
                "outcome": _clean_case_str(self.outcome, _CASE_TEXT_MAX_CHARS),
                "model": _clean_case_str(self.model, 80),
            }
        )


class ProcessedArticle(BaseModel):
    """Article after processing; input for search/embeddings later.

    Produced by the article-processing LangGraph agent. LLM summarization
    and vector embeddings can fill optional fields in later stages.
    """

    title: str
    link: str
    published: datetime | None = None
    source_id: str
    source_name: str
    channel: Channel = Field(
        default="news",
        description="Provenance lane: news | filings | tips",
    )
    summary: str | None = None
    clean_text: str = Field(..., description="Normalized plain text used for scoring/search")
    entities: ExtractedEntities = Field(default_factory=ExtractedEntities)
    relevance_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Heuristic threat-relevance score in [0, 1]",
    )
    itm_alignment: Literal["insider", "weak"] = Field(
        default="weak",
        description=("ITM-aligned insider scenario (insider) vs weak/unaligned mapping (weak)"),
    )
    story_key: str = Field(
        default="",
        description="Fingerprint for multi-source stream clustering (title+day)",
    )
    use_cases: list[str] = Field(
        default_factory=list,
        description="Matched hunt use-case ids (e.g. overemployment, data-exfiltration)",
    )
    insider_type: InsiderType | None = Field(
        default=None,
        description="Inferred insider disposition; None = unclassified",
    )
    classification_source: Literal["heuristic", "llm"] | None = Field(
        default=None,
        description="Which classifier produced use_cases/insider_type",
    )
    classification_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="LLM classifier self-reported confidence (heuristic leaves None)",
    )
    processed_at: datetime = Field(default_factory=_utc_now)
    # Filled by the optional ingest summarizer LLM (SUMMARIZER_LLM_PROVIDER)
    ai_summary: str | None = None
    case_record: CaseRecord | None = Field(
        default=None,
        description="Structured case facts (derived from forensics for UI back-compat)",
    )
    forensics: PerCaseForensics | None = Field(
        default=None,
        description="Ingest-time forensic reconstruction from the unified enricher LLM",
    )
    discovery: CaseDiscovery | None = Field(
        default=None,
        description="Second-pass novel-technique assessment (independent of forensics)",
    )
    embedding: list[float] | None = None


class ProcessingRunResult(BaseModel):
    """Outcome of processing raw articles into ProcessedArticle records."""

    started_at: datetime
    finished_at: datetime | None = None
    articles_read: int = 0
    articles_processed: int = 0
    articles_saved: int = 0
    articles_skipped: int = 0
    reenrich_cleared: int = 0
    errors: list[str] = Field(default_factory=list)
