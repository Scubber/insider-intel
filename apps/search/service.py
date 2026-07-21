"""Search service singleton used by the API and CLI."""

from __future__ import annotations

import logging
from pathlib import Path

from apps.aggregator.config import DEFAULT_FEEDS
from apps.search.index import ArticleSearchIndex
from shared.itm.index import load_itm_index
from shared.schemas import (
    ArticleListResponse,
    CandidateCatalogResponse,
    ControlRef,
    ItmCatalogResponse,
    SearchMode,
    SearchResponse,
    SocialCatalogResponse,
    SocialSourceInfo,
    SourceInfo,
    UseCaseInfo,
)
from shared.schemas.articles import resolve_channel
from shared.schemas.search import ItmArticleSummary, ItmTechniqueSummary
from shared.settings import get_settings

logger = logging.getLogger(__name__)

_index: ArticleSearchIndex | None = None
_index_path: str | None = None


def get_index(path: str | Path | None = None, *, reload: bool = False) -> ArticleSearchIndex:
    """Return the shared search index, loading from disk if needed."""
    global _index, _index_path

    settings = get_settings()
    resolved = str(path or settings.processed_articles_path)

    if _index is None or reload or _index_path != resolved:
        logger.info("Loading search index from %s", resolved)
        _index = ArticleSearchIndex.from_jsonl(resolved)
        _index_path = resolved
    return _index


def list_sources(
    path: str | Path | None = None,
    *,
    min_score: float = 0.0,
    theme: str | None = None,
    itm_id: str | None = None,
    itm_alignment: str = "all",
    channel: str = "all",
    use_case: str | None = None,
    insider_type: str = "all",
) -> list[SourceInfo]:
    """Sources with article counts for the active stream filters.

    When filters are applied, only sources with matching articles are returned
    (counts reflect the filtered set). Unfiltered calls still merge configured feeds.
    """
    configured = {f.id: f for f in DEFAULT_FEEDS}
    indexed = {
        sid: (name, count)
        for sid, name, count in get_index(path).list_sources(
            min_score=min_score,
            theme=theme,
            itm_id=itm_id,
            itm_alignment=itm_alignment,
            channel=channel,
            use_case=use_case,
            insider_type=insider_type,
        )
    }

    filtered = (
        (itm_alignment or "all").strip().lower() not in {"", "all", "*"}
        or bool(theme or itm_id or min_score > 0)
        or (channel or "all").strip().lower() not in {"", "all", "*"}
        or (use_case or "all").strip().lower() not in {"", "all", "*"}
        or (insider_type or "all").strip().lower() not in {"", "all", "*"}
    )

    merged: dict[str, SourceInfo] = {}
    if not filtered:
        for sid, feed in configured.items():
            count = indexed.get(sid, ("", 0))[1]
            merged[sid] = SourceInfo(
                id=feed.id,
                name=feed.name,
                url=str(feed.url),
                category=feed.category,
                channel=resolve_channel(feed.id, feed.channel, category=feed.category),
                enabled=feed.enabled,
                article_count=count,
            )
    for sid, (name, count) in indexed.items():
        if sid in merged:
            merged[sid].article_count = count
            continue
        feed = configured.get(sid)
        merged[sid] = SourceInfo(
            id=sid,
            name=(feed.name if feed else None) or name or sid,
            url=str(feed.url) if feed else None,
            category=(feed.category if feed else None) or "ingested",
            channel=resolve_channel(
                sid,
                feed.channel if feed else None,
                category=(feed.category if feed else None),
            ),
            enabled=feed.enabled if feed else True,
            article_count=count,
        )
    return sorted(merged.values(), key=lambda s: s.name.lower())


def search(
    query: str,
    *,
    mode: SearchMode | str = SearchMode.hybrid,
    limit: int = 10,
    min_score: float = 0.0,
    source_id: str | None = None,
    theme: str | None = None,
    itm_id: str | None = None,
    itm_alignment: str = "insider",
    channel: str = "all",
    use_case: str | None = None,
    insider_type: str = "all",
    path: str | Path | None = None,
) -> SearchResponse:
    if isinstance(mode, str):
        mode = SearchMode(mode)
    index = get_index(path)
    return index.search(
        query,
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


def list_articles(
    *,
    limit: int = 50,
    min_score: float = 0.0,
    source_id: str | None = None,
    theme: str | None = None,
    itm_id: str | None = None,
    detection_id: str | None = None,
    prevention_id: str | None = None,
    itm_alignment: str = "insider",
    channel: str = "all",
    use_case: str | None = None,
    insider_type: str = "all",
    topic_match: bool = False,
    group: bool = True,
    path: str | Path | None = None,
) -> ArticleListResponse:
    index = get_index(path)
    return index.list_articles(
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


def _social_store():
    from apps.aggregator.social_subscriptions import SocialSubscriptionStore

    return SocialSubscriptionStore(get_settings().social_subscriptions_path)


def social_catalog() -> SocialCatalogResponse:
    """Curated suggestions + current subscriptions, with indexed article counts."""
    from apps.aggregator.social_catalog import build_catalog, subscription_to_info

    counts = {sid: count for sid, _name, count in get_index().list_sources(channel="social")}
    subscriptions = [subscription_to_info(s) for s in _social_store().list()]
    subscribed_keys = {(s.platform, s.id) for s in subscriptions if s.subscribed}

    suggestions = build_catalog()
    for info in suggestions:
        info.subscribed = (info.platform, info.id) in subscribed_keys
        info.article_count = counts.get(info.source_id, 0)
    for info in subscriptions:
        info.article_count = counts.get(info.source_id, 0)
    return SocialCatalogResponse(suggestions=suggestions, subscriptions=subscriptions)


def add_social_subscription(
    platform: str,
    handle: str,
    *,
    name: str | None = None,
    use_cases: list[str] | None = None,
) -> SocialSourceInfo:
    from apps.aggregator.social_catalog import build_catalog, subscription_to_info
    from apps.aggregator.social_subscriptions import normalize_handle

    origin = "manual"
    catalog_use_cases: list[str] | None = None
    normalized = normalize_handle(platform, handle)
    for info in build_catalog():
        if info.platform == platform and info.id == normalized:
            origin = "catalog"
            catalog_use_cases = list(info.use_cases)
            break
    entry = _social_store().add(
        platform,
        handle,
        name=name,
        origin=origin,
        use_cases=use_cases or catalog_use_cases,
    )
    return subscription_to_info(entry)


def remove_social_subscription(platform: str, handle: str) -> bool:
    return _social_store().remove(platform, handle)


def list_use_cases() -> list[UseCaseInfo]:
    from shared.taxonomy.use_cases import USE_CASES

    return [UseCaseInfo(id=uc.id, label=uc.label, description=uc.description) for uc in USE_CASES]


def trending(*, window_days: int = 7, limit: int = 8) -> list[dict]:
    """Most-active topics across the indexed feeds (week-over-week deltas)."""
    return get_index().trending(window_days=window_days, limit=limit)


def candidate_catalog() -> CandidateCatalogResponse:
    """The novel-technique candidate view (job-written state; read-only here)."""
    from apps.aggregator.technique_seeds import TechniqueSeedStore

    return TechniqueSeedStore(get_settings().technique_seeds_path).read()


def itm_catalog(
    *,
    source_id: str | None = None,
    channel: str = "all",
) -> ItmCatalogResponse:
    from shared.itm.controls import list_detection_catalog, list_prevention_catalog

    index = load_itm_index()
    article_counts = get_index().technique_article_counts(
        topic_match=False,
        itm_alignment="all",
        min_score=0.0,
        source_id=source_id,
        channel=channel,
    )
    return ItmCatalogResponse(
        itm_version=index.itm_version,
        refreshed_at=index.refreshed_at,
        articles=[ItmArticleSummary(id=a.id, title=a.title, theme=a.theme) for a in index.articles],
        techniques=[
            ItmTechniqueSummary(
                id=t.id,
                title=t.title,
                theme=t.theme,
                article_id=t.article_id,
                parent_id=t.parent_id,
                description=t.description_text or "",
                aliases=list(t.aliases or []),
                article_count=int(article_counts.get(t.id, 0)),
                detections=[ControlRef(id=c.id, title=c.title) for c in t.detections],
                preventions=[ControlRef(id=c.id, title=c.title) for c in t.preventions],
            )
            for t in index.techniques
        ],
        detections=list_detection_catalog(),
        preventions=list_prevention_catalog(),
    )
