"""Search request/response schemas."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from shared.schemas.articles import CaseRecord, ControlRef, ItmHit
from shared.schemas.forensics import PerCaseForensics


class SearchMode(StrEnum):
    keyword = "keyword"
    semantic = "semantic"
    hybrid = "hybrid"


class SearchHit(BaseModel):
    title: str
    link: str
    source_id: str
    source_name: str
    channel: str = Field(
        default="news",
        description="Provenance lane: news | filings | tips",
    )
    published: datetime | None = None
    summary: str | None = None
    relevance_score: float = 0.0
    score: float = Field(..., description="Search ranking score for this query")
    cves: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    keywords_hit: list[str] = Field(default_factory=list)
    operator_terms: list[str] = Field(default_factory=list)
    itm_hits: list[ItmHit] = Field(default_factory=list)
    related_detections: list[ControlRef] = Field(default_factory=list)
    related_preventions: list[ControlRef] = Field(default_factory=list)
    itm_alignment: str = Field(
        default="weak",
        description="ITM-aligned insider scenario (insider) or weak mapping (weak)",
    )
    story_key: str = Field(
        default="",
        description="Fingerprint for multi-source stream clustering",
    )
    use_cases: list[str] = Field(
        default_factory=list,
        description="Matched hunt use-case ids (e.g. overemployment)",
    )
    insider_type: str | None = Field(
        default=None,
        description="negligent | malicious | unintentional | None (unclassified)",
    )
    ai_summary: str | None = Field(
        default=None,
        description="Analyst-style summary from the ingest summarizer LLM",
    )
    case_record: CaseRecord | None = Field(
        default=None,
        description="Structured case facts from the ingest summarizer LLM",
    )
    forensics: PerCaseForensics | None = Field(
        default=None,
        description="Ingest-time forensic reconstruction (unified enricher LLM)",
    )


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Search query text")
    mode: SearchMode = SearchMode.hybrid
    limit: int = Field(default=10, ge=1, le=100)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)
    source_id: str | None = None
    theme: str | None = Field(
        default=None,
        description="ITM theme filter: motive|means|preparation|infringement|anti-forensics",
    )
    itm_id: str | None = Field(
        default=None,
        description="ITM technique id filter (e.g. IF001, ME005)",
    )
    itm_alignment: str = Field(
        default="insider",
        description="Filter: insider (default) | weak | all",
    )
    channel: str = Field(
        default="all",
        description="Provenance filter: news | filings | tips | social | all (default)",
    )
    use_case: str | None = Field(
        default=None,
        description="Use-case filter id (e.g. overemployment) or None/all",
    )
    insider_type: str = Field(
        default="all",
        description="negligent | malicious | unintentional | none | all (default)",
    )


class SearchResponse(BaseModel):
    query: str
    mode: SearchMode
    total_indexed: int
    count: int
    results: list[SearchHit]


class StoryCluster(BaseModel):
    """One stream card: best matching member + sibling sources."""

    story_key: str
    channel: str = "news"
    primary: SearchHit
    siblings: list[SearchHit] = Field(default_factory=list)
    member_count: int = 1


class ArticleListResponse(BaseModel):
    """Chronological article stream for the Feedly-style reader."""

    total_indexed: int
    count: int
    results: list[SearchHit]
    clusters: list[StoryCluster] = Field(
        default_factory=list,
        description="Story clusters when group=true (results = primaries)",
    )


class SourceInfo(BaseModel):
    """Source shown in the UI sidebar (configured feed and/or ingested)."""

    id: str
    name: str
    url: str | None = None
    category: str | None = None
    channel: str = Field(
        default="news",
        description="Provenance lane: news | filings | tips",
    )
    enabled: bool = True
    article_count: int = 0


class SocialSourceInfo(BaseModel):
    """Curated or subscribed social source (subreddit / X account)."""

    platform: str = Field(..., description="reddit | x")
    id: str = Field(..., description="Subreddit name or X handle (no prefix)")
    name: str
    url: str | None = None
    description: str | None = None
    use_cases: list[str] = Field(default_factory=list)
    source_id: str = Field(..., description="Computed ingest source id (social-*)")
    subscribed: bool = False
    origin: str = Field(default="catalog", description="catalog | manual")
    article_count: int = 0


class SocialCatalogResponse(BaseModel):
    """Discovery catalog + subscription state for the social picker."""

    suggestions: list[SocialSourceInfo] = Field(default_factory=list)
    subscriptions: list[SocialSourceInfo] = Field(default_factory=list)


class UseCaseInfo(BaseModel):
    """Hunt use case exposed to the UI filter chips."""

    id: str
    label: str
    description: str = ""


class ItmTechniqueSummary(BaseModel):
    id: str
    title: str
    theme: str
    article_id: str
    parent_id: str | None = None
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    article_count: int = 0
    detections: list[ControlRef] = Field(default_factory=list)
    preventions: list[ControlRef] = Field(default_factory=list)


class ItmArticleSummary(BaseModel):
    id: str
    title: str
    theme: str


class ItmCatalogResponse(BaseModel):
    """Slim Insider Threat Matrix™ catalog for UI filters."""

    itm_version: str | None = None
    refreshed_at: str | None = None
    articles: list[ItmArticleSummary] = Field(default_factory=list)
    techniques: list[ItmTechniqueSummary] = Field(default_factory=list)
    detections: list[ControlRef] = Field(default_factory=list)
    preventions: list[ControlRef] = Field(default_factory=list)
    attribution: str = (
        "Insider Threat Matrix™ is owned by Forscie Limited. "
        "Insider Threat Matrix is a trademark of Forscie Limited."
    )
