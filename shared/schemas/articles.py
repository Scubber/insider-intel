"""Pydantic models for RSS ingestion and raw article storage."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl

# Provenance lane for stream filters (orthogonal to Insider Focus).
Channel = Literal["news", "filings", "tips"]


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
        description=(
            "ITM-aligned insider scenario (insider) vs weak/unaligned mapping (weak)"
        ),
    )
    story_key: str = Field(
        default="",
        description="Fingerprint for multi-source stream clustering (title+day)",
    )
    processed_at: datetime = Field(default_factory=_utc_now)
    # Filled by later LLM / embedding stages
    ai_summary: str | None = None
    embedding: list[float] | None = None


class ProcessingRunResult(BaseModel):
    """Outcome of processing raw articles into ProcessedArticle records."""

    started_at: datetime
    finished_at: datetime | None = None
    articles_read: int = 0
    articles_processed: int = 0
    articles_saved: int = 0
    articles_skipped: int = 0
    errors: list[str] = Field(default_factory=list)
