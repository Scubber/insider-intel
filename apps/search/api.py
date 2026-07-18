"""FastAPI search + reader API for threat-intel articles."""

from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from apps.aggregator.export import EXPORT_SCHEMA_VERSION, article_to_export_row, filter_articles
from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search import service
from apps.search.ratelimit import SlidingWindowLimiter
from apps.search.ttp_extract import ExtractTtpsRequest, ExtractTtpsResponse, extract_ttps_for_links
from shared.schemas import (
    ArticleListResponse,
    ItmCatalogResponse,
    SearchHit,
    SearchMode,
    SearchRequest,
    SearchResponse,
    SocialCatalogResponse,
    SocialSourceInfo,
    SourceInfo,
    UseCaseInfo,
)
from shared.settings import get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Best-effort load; empty index is valid until articles are processed
    service.get_index()
    yield


app = FastAPI(
    title="insider-intel",
    description=(
        "Insider-threat OSINT aggregator aligned to the Insider Threat Matrix™: "
        "article stream, ITM tagging, hunt keyword minting, and one-way corporate export."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_export_token(
    authorization: str | None = Header(default=None),
) -> None:
    """Corporate pull auth — EXPORT_API_TOKEN bearer required when configured."""
    expected = get_settings().export_api_token
    if not expected or not expected.strip():
        raise HTTPException(
            status_code=503,
            detail="Export API disabled (set EXPORT_API_TOKEN to enable)",
        )
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    provided = authorization.split(" ", 1)[1].strip()
    if not secrets.compare_digest(provided, expected.strip()):
        raise HTTPException(status_code=403, detail="Invalid export token")


@app.get("/health")
def health() -> dict[str, object]:
    index = service.get_index()
    last = index.last_processed_at
    return {
        "status": "ok",
        "indexed_articles": index.size,
        "last_indexed_at": last.isoformat() if last else None,
    }


@app.get("/sources", response_model=list[SourceInfo])
def list_sources(
    min_score: float = Query(default=0.0, ge=0.0, le=1.0),
    theme: str | None = Query(
        default=None,
        description="ITM theme: motive|means|preparation|infringement|anti-forensics",
    ),
    itm_id: str | None = Query(
        default=None,
        description="ITM technique id (e.g. IF001, ME005)",
    ),
    itm_alignment: str = Query(
        default="all",
        description="ITM alignment filter: insider | weak | all (default all)",
    ),
    channel: str = Query(
        default="all",
        description="Provenance filter: news | filings | tips | social | all (default)",
    ),
    use_case: str | None = Query(
        default=None,
        description="Use-case filter (e.g. overemployment); see GET /usecases",
    ),
    insider_type: str = Query(
        default="all",
        description="negligent | malicious | unintentional | none | all (default)",
    ),
) -> list[SourceInfo]:
    """Sources with counts matching the same filters as the article stream."""
    return service.list_sources(
        min_score=min_score,
        theme=theme,
        itm_id=itm_id,
        itm_alignment=itm_alignment,
        channel=channel,
        use_case=use_case,
        insider_type=insider_type,
    )


@app.get("/itm", response_model=ItmCatalogResponse)
def list_itm(
    source_id: str | None = Query(
        default=None,
        description="When set, technique article_count is scoped to this source",
    ),
    channel: str = Query(
        default="all",
        description="When not all, technique article_count is scoped to this channel",
    ),
) -> ItmCatalogResponse:
    """Return slim Insider Threat Matrix™ catalog for UI filters."""
    return service.itm_catalog(source_id=source_id, channel=channel)


@app.get("/articles", response_model=ArticleListResponse)
def list_articles(
    limit: int = Query(default=50, ge=1, le=200),
    min_score: float = Query(default=0.0, ge=0.0, le=1.0),
    source_id: str | None = None,
    theme: str | None = Query(
        default=None,
        description="ITM theme: motive|means|preparation|infringement|anti-forensics",
    ),
    itm_id: str | None = Query(
        default=None,
        description="ITM technique id (e.g. IF001, ME005)",
    ),
    detection_id: str | None = Query(
        default=None,
        description="ITM detection id (e.g. DT021); reverse-join via linked techniques",
    ),
    prevention_id: str | None = Query(
        default=None,
        description="ITM prevention id (e.g. PV037); reverse-join via linked techniques",
    ),
    itm_alignment: str = Query(
        default="insider",
        description="ITM alignment filter: insider (default) | weak | all",
    ),
    channel: str = Query(
        default="all",
        description="Provenance filter: news | filings | tips | social | all (default)",
    ),
    use_case: str | None = Query(
        default=None,
        description="Use-case filter (e.g. overemployment); see GET /usecases",
    ),
    insider_type: str = Query(
        default="all",
        description="negligent | malicious | unintentional | none | all (default)",
    ),
    topic_match: bool = Query(
        default=False,
        description=(
            "When itm_id/detection_id/prevention_id is set, also include articles "
            "whose text matches linked technique title/aliases"
        ),
    ),
    group: bool = Query(
        default=True,
        description="Collapse multi-source same-day stories within a channel",
    ),
) -> ArticleListResponse:
    """Chronological article stream (Feedly-style reader)."""
    return service.list_articles(
        limit=limit,
        min_score=min_score,
        source_id=source_id,
        theme=theme,
        itm_id=itm_id,
        detection_id=detection_id,
        prevention_id=prevention_id,
        itm_alignment=itm_alignment,
        channel=channel,
        use_case=use_case,
        insider_type=insider_type,
        topic_match=topic_match,
        group=group,
    )


@app.post("/search", response_model=SearchResponse)
def search_post(body: SearchRequest) -> SearchResponse:
    return service.search(
        body.query,
        mode=body.mode,
        limit=body.limit,
        min_score=body.min_score,
        source_id=body.source_id,
        theme=body.theme,
        itm_id=body.itm_id,
        itm_alignment=body.itm_alignment,
        channel=body.channel,
        use_case=body.use_case,
        insider_type=body.insider_type,
    )


@app.get("/search", response_model=SearchResponse)
def search_get(
    q: str = Query(..., min_length=1, description="Search query"),
    mode: SearchMode = SearchMode.hybrid,
    limit: int = Query(default=10, ge=1, le=100),
    min_score: float = Query(default=0.0, ge=0.0, le=1.0),
    source_id: str | None = None,
    theme: str | None = None,
    itm_id: str | None = None,
    itm_alignment: str = Query(
        default="insider",
        description="ITM alignment filter: insider (default) | weak | all",
    ),
    channel: str = Query(
        default="all",
        description="Provenance filter: news | filings | tips | social | all (default)",
    ),
    use_case: str | None = Query(
        default=None,
        description="Use-case filter (e.g. overemployment); see GET /usecases",
    ),
    insider_type: str = Query(
        default="all",
        description="negligent | malicious | unintentional | none | all (default)",
    ),
) -> SearchResponse:
    return service.search(
        q,
        mode=mode,
        limit=limit,
        min_score=min_score,
        source_id=source_id,
        theme=theme,
        itm_id=itm_id,
        itm_alignment=itm_alignment,
        channel=channel,
        use_case=use_case,
        insider_type=insider_type,
    )


@app.get("/export/articles")
def export_articles(
    min_score: float = Query(default=0.0, ge=0.0, le=1.0),
    since: str | None = Query(
        default=None,
        description="ISO datetime; only articles on/after this stamp",
    ),
    itm_alignment: str = Query(
        default="insider",
        description="ITM alignment filter: insider (default) | weak | all",
    ),
    format: str = Query(default="json", pattern="^(json|ndjson)$"),
    _auth: None = Depends(_require_export_token),
):
    """One-way corporate pull of processed articles (no corp credentials stored here)."""
    settings = get_settings()
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=UTC)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid since datetime") from exc

    articles = filter_articles(
        JsonlProcessedStore(settings.processed_articles_path).load_all(),
        min_score=min_score,
        since=since_dt,
        itm_alignment=itm_alignment,
    )
    rows = [article_to_export_row(a) for a in articles]
    if format == "ndjson":
        body = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
        if body:
            body += "\n"
        return PlainTextResponse(
            body,
            media_type="application/x-ndjson",
            headers={
                "X-Export-Schema": EXPORT_SCHEMA_VERSION,
                "X-Article-Count": str(len(rows)),
            },
        )
    return JSONResponse(
        {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "count": len(rows),
            "itm_alignment": itm_alignment,
            "results": rows,
        }
    )


class ArticlesByLinksRequest(BaseModel):
    links: list[str] = Field(default_factory=list, min_length=1, max_length=40)


class ArticlesByLinksResponse(BaseModel):
    results: list[SearchHit] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


@app.post("/articles/by-links")
def articles_by_links(body: ArticlesByLinksRequest) -> ArticlesByLinksResponse:
    """Resolve exact article links to indexed hits (shared-board hydration)."""
    links = [str(link).strip() for link in body.links if str(link).strip()]
    if not links:
        raise HTTPException(status_code=400, detail="links required")
    index = service.get_index()
    results = []
    missing = []
    for link in links:
        hit = index.hit_by_link(link)
        if hit is None:
            missing.append(link)
        else:
            results.append(hit)
    return ArticlesByLinksResponse(results=results, missing=missing)


class ArticleTextResponse(BaseModel):
    title: str
    link: str
    channel: str = "news"
    text: str = ""


@app.get("/articles/text", response_model=ArticleTextResponse)
def article_text(link: str = Query(..., min_length=8)) -> ArticleTextResponse:
    """Full stored document text for one indexed article (read-the-filing).

    Court cases carry their backfilled RECAP/opinion bodies in clean_text,
    which the list endpoints deliberately omit — this returns it on demand.
    """
    from shared.schemas.articles import resolve_channel

    article = service.get_index().get_by_link(link.strip())
    if article is None:
        raise HTTPException(status_code=404, detail="link not in index")
    # Prefer the raw store's content: it keeps the document's real line breaks,
    # which processing's clean_text deliberately collapses for matching.
    text = ""
    try:
        from apps.aggregator.storage import JsonlArticleStore

        store = JsonlArticleStore(get_settings().raw_articles_path)
        for raw in store.load_all():
            if raw.link == article.link:
                text = raw.content or ""
                break
    except Exception:  # noqa: BLE001 — raw store is best-effort enrichment
        text = ""
    if not text:
        text = article.clean_text or ""
    # Drop the ingest query-marker line so readers see the document, not our tag.
    text = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("CourtListener query:")
    ).strip()
    return ArticleTextResponse(
        title=article.title,
        link=article.link,
        channel=resolve_channel(article.source_id, getattr(article, "channel", None)),
        text=text,
    )


_extract_limiter: SlidingWindowLimiter | None = None


def _get_extract_limiter() -> SlidingWindowLimiter | None:
    global _extract_limiter
    settings = get_settings()
    if settings.extract_rate_per_ip_hour <= 0:
        return None
    if _extract_limiter is None:
        _extract_limiter = SlidingWindowLimiter(
            settings.extract_rate_per_ip_hour,
            settings.extract_rate_global_day,
        )
    return _extract_limiter


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.post("/extract/ttps")
def extract_ttps(body: ExtractTtpsRequest, request: Request) -> ExtractTtpsResponse:
    """Build a multi-channel hunt report from extraction-board article links.

    Assembles each article's stored ingest-time forensic record (or a
    floor-derived one if not yet enriched) into technique sections in code —
    no LLM at read time. The rate limiter is only a CPU/abuse guard.
    """
    limiter = _get_extract_limiter()
    if limiter is not None and not limiter.allow(_client_ip(request)):
        raise HTTPException(
            status_code=429,
            detail="extract rate limit exceeded; try again later",
        )
    links = [str(link).strip() for link in body.links if str(link).strip()]
    if not links:
        raise HTTPException(status_code=400, detail="links required")
    index = service.get_index()
    return extract_ttps_for_links(index, links, settings=get_settings())


@app.get("/usecases", response_model=list[UseCaseInfo])
def list_usecases() -> list[UseCaseInfo]:
    """Hunt use-case registry (ids + labels for UI filter chips)."""
    return service.list_use_cases()


@app.get("/social/catalog", response_model=SocialCatalogResponse)
def social_catalog() -> SocialCatalogResponse:
    """Curated social discovery suggestions plus current subscriptions."""
    return service.social_catalog()


class SocialSubscriptionRequest(BaseModel):
    platform: str = Field(..., pattern="^(reddit|x)$")
    id: str = Field(..., min_length=1, description="Subreddit name or X handle")
    name: str | None = None


@app.get("/social/subscriptions", response_model=list[SocialSourceInfo])
def list_social_subscriptions() -> list[SocialSourceInfo]:
    return service.social_catalog().subscriptions


@app.post("/social/subscriptions", response_model=SocialSourceInfo)
def add_social_subscription(body: SocialSubscriptionRequest) -> SocialSourceInfo:
    """Subscribe a subreddit / X handle; pulled on the next social ingest run."""
    try:
        return service.add_social_subscription(body.platform, body.id, name=body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/social/subscriptions/{platform}/{handle}")
def remove_social_subscription(platform: str, handle: str) -> dict[str, object]:
    if platform not in ("reddit", "x"):
        raise HTTPException(status_code=400, detail="platform must be reddit or x")
    removed = service.remove_social_subscription(platform, handle)
    if not removed:
        raise HTTPException(status_code=404, detail="subscription not found")
    return {"status": "removed", "platform": platform, "id": handle}


class SocialIngestUrlRequest(BaseModel):
    url: str = Field(..., min_length=10, description="Reddit post URL (or /s/ share link)")


@app.post("/social/ingest_url")
def social_ingest_url(body: SocialIngestUrlRequest) -> dict[str, object]:
    """Flag one social post by URL: fetch, store, process, and index it now."""
    from apps.aggregator.process_pipeline import run_processing
    from apps.aggregator.reddit_pipeline import ingest_reddit_post_url

    settings = get_settings()
    try:
        article = ingest_reddit_post_url(body.url, store_path=settings.raw_articles_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — remote fetch failure
        raise HTTPException(status_code=502, detail=f"fetch failed: {exc}") from exc
    if article is None:
        raise HTTPException(status_code=404, detail="no post found at that URL")

    processing = run_processing(
        raw_path=settings.raw_articles_path,
        processed_path=settings.processed_articles_path,
        min_score=0.0,
    )
    index = service.get_index(settings.processed_articles_path, reload=True)
    hit = index.hit_by_link(article.link)
    return {
        "status": "ingested",
        "title": article.title,
        "link": article.link,
        "channel": article.channel,
        "processed": processing.articles_processed,
        "use_cases": hit.use_cases if hit else [],
        "insider_type": hit.insider_type if hit else None,
    }


@app.post("/reload")
def reload_index() -> dict[str, object]:
    settings = get_settings()
    try:
        index = service.get_index(settings.processed_articles_path, reload=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "reloaded", "indexed_articles": index.size}
