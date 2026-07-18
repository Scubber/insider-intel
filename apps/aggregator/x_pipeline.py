"""Ingest subscribed X handles into raw storage.

Auth: X_BEARER_TOKEN directly, or minted from X_CONSUMER_KEY/SECRET.
Cadence: X_INGEST_EVERY_HOURS (default 48) keeps the free tier's ~100
post-reads/month intact; the watermark lives in the ingest state.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from apps.aggregator.ingest_state import JsonIngestState
from apps.aggregator.pipeline import DEFAULT_STORE_PATH
from apps.aggregator.social_subscriptions import SocialSubscriptionStore
from apps.aggregator.storage import ArticleStore, JsonlArticleStore
from apps.aggregator.x_client import (
    XClient,
    handle_source,
    mint_bearer_token,
    tweet_to_article,
)
from shared.schemas import IngestionRunResult, RawArticle, SourceIngestionResult
from shared.settings import get_settings

logger = logging.getLogger(__name__)

_LAST_RUN_KEY = "x:last_ingest"


def _resolve_bearer(settings) -> str | None:
    if settings.x_bearer_token:
        return settings.x_bearer_token
    if settings.x_consumer_key and settings.x_consumer_secret:
        return mint_bearer_token(settings.x_consumer_key, settings.x_consumer_secret)
    return None


def _within_cadence(state: JsonIngestState, every_hours: int, now: datetime) -> bool:
    """True when the last pull is recent enough that this run should skip."""
    if every_hours <= 0:
        return False
    stored = state.get(_LAST_RUN_KEY)
    if not stored:
        return False
    try:
        last = datetime.fromisoformat(stored)
    except ValueError:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (now - last) < timedelta(hours=every_hours)


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
    state: JsonIngestState | None = None,
) -> IngestionRunResult:
    """Pull recent posts for each subscribed X handle into JSONL storage.

    Pass ``state`` (the scheduled-refresh path does) to enforce the
    X_INGEST_EVERY_HOURS cadence; direct CLI/test calls without it always run.
    """
    settings = get_settings()
    started = datetime.now(UTC)
    resolved = resolve_handles(handles)

    def empty() -> IngestionRunResult:
        return IngestionRunResult(
            started_at=started,
            finished_at=datetime.now(UTC),
            sources=[],
            total_articles_saved=0,
        )

    if state is not None and _within_cadence(state, settings.x_ingest_every_hours, started):
        logger.info(
            "Skipping X ingest: within the %dh cadence window (free-tier quota guard)",
            settings.x_ingest_every_hours,
        )
        return empty()

    if client is None:
        bearer = _resolve_bearer(settings)
        if not bearer:
            if resolved:
                logger.info(
                    "Skipping %d X handle(s): no X_BEARER_TOKEN or "
                    "X_CONSUMER_KEY/SECRET configured (v2 read access needs a "
                    "developer app; free tier is ~100 post-reads/month)",
                    len(resolved),
                )
            return empty()
        client = XClient(bearer_token=bearer)

    if state is not None:
        state.set(_LAST_RUN_KEY, started.isoformat())

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
