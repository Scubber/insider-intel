"""Ingest subscribed X handles into raw storage (requires X_BEARER_TOKEN)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from apps.aggregator.pipeline import DEFAULT_STORE_PATH
from apps.aggregator.social_subscriptions import SocialSubscriptionStore
from apps.aggregator.storage import ArticleStore, JsonlArticleStore
from apps.aggregator.x_client import XClient, handle_source, tweet_to_article
from shared.schemas import IngestionRunResult, RawArticle, SourceIngestionResult
from shared.settings import get_settings

logger = logging.getLogger(__name__)


def resolve_handles(
    handles: list[str] | None = None,
    *,
    subscriptions_path: str | None = None,
) -> list[str]:
    """Explicit arg -> subscription store -> X_HANDLES fallback."""
    if handles:
        return handles
    settings = get_settings()
    store = SocialSubscriptionStore(subscriptions_path or settings.social_subscriptions_path)
    subscribed = [s.id for s in store.enabled("x")]
    if subscribed:
        return subscribed
    return settings.x_handle_list()


def run_x_ingestion(
    *,
    handles: list[str] | None = None,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    include_raw: bool = False,
    max_results: int | None = None,
    client: XClient | None = None,
) -> IngestionRunResult:
    """Pull recent posts for each subscribed X handle into JSONL storage."""
    settings = get_settings()
    started = datetime.now(UTC)
    resolved = resolve_handles(handles)

    if client is None:
        if not settings.x_bearer_token:
            if resolved:
                logger.info(
                    "Skipping %d X handle(s): X_BEARER_TOKEN not configured "
                    "(X API v2 read access requires a paid tier)",
                    len(resolved),
                )
            return IngestionRunResult(
                started_at=started,
                finished_at=datetime.now(UTC),
                sources=[],
                total_articles_saved=0,
            )
        client = XClient(bearer_token=settings.x_bearer_token)

    article_store = store or JsonlArticleStore(store_path)
    count = max_results if max_results is not None else settings.x_max_results

    sources: list[SourceIngestionResult] = []
    total_saved = 0
    for handle in resolved:
        source_id, source_name = handle_source(handle)
        try:
            tweets = client.recent_tweets(handle, max_results=count)
            articles: list[RawArticle] = []
            for tweet in tweets:
                fields = tweet_to_article(tweet, handle, include_raw=include_raw)
                if fields is None:
                    continue
                articles.append(RawArticle(**fields))
            refresh = getattr(article_store, "refresh", None)
            if callable(refresh):
                new_count, updated = refresh(articles)
                saved = new_count + updated
            else:
                saved = article_store.save(articles)
            total_saved += saved
            sources.append(
                SourceIngestionResult(
                    source_id=source_id,
                    source_name=source_name,
                    success=True,
                    articles_fetched=len(articles),
                    articles_saved=saved,
                )
            )
        except Exception as exc:  # noqa: BLE001 — isolate per-handle failures
            logger.exception("X ingestion failed for @%s", handle)
            sources.append(
                SourceIngestionResult(
                    source_id=source_id,
                    source_name=source_name,
                    success=False,
                    error=str(exc),
                )
            )

    return IngestionRunResult(
        started_at=started,
        finished_at=datetime.now(UTC),
        sources=sources,
        total_articles_saved=total_saved,
    )
