"""Ingest subscribed subreddits (and single posts by URL) into raw storage."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from apps.aggregator.pipeline import DEFAULT_STORE_PATH
from apps.aggregator.reddit import (
    fetch_post_by_url,
    fetch_subreddit_new,
    post_to_article,
    subreddit_source,
)
from apps.aggregator.social_subscriptions import SocialSubscriptionStore
from apps.aggregator.storage import ArticleStore, JsonlArticleStore
from shared.schemas import IngestionRunResult, RawArticle, SourceIngestionResult
from shared.settings import get_settings

logger = logging.getLogger(__name__)


def resolve_subreddits(
    subreddits: list[str] | None = None,
    *,
    subscriptions_path: str | None = None,
) -> list[str]:
    """Explicit arg -> subscription store -> REDDIT_SUBREDDITS fallback."""
    if subreddits:
        return subreddits
    settings = get_settings()
    store = SocialSubscriptionStore(subscriptions_path or settings.social_subscriptions_path)
    subscribed = [s.id for s in store.enabled("reddit")]
    if subscribed:
        return subscribed
    return settings.reddit_subreddit_list()


def run_reddit_ingestion(
    *,
    subreddits: list[str] | None = None,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    include_raw: bool = False,
    limit: int | None = None,
    delay_seconds: float | None = None,
) -> IngestionRunResult:
    """Pull /new for each subscribed subreddit into JSONL storage."""
    settings = get_settings()
    started = datetime.now(UTC)
    article_store = store or JsonlArticleStore(store_path)
    subs = resolve_subreddits(subreddits)
    max_posts = limit if limit is not None else settings.reddit_limit
    delay = settings.reddit_delay_seconds if delay_seconds is None else delay_seconds

    sources: list[SourceIngestionResult] = []
    total_saved = 0
    for position, sub in enumerate(subs):
        source_id, source_name = subreddit_source(sub)
        try:
            if position > 0 and delay > 0:
                time.sleep(delay)
            posts = fetch_subreddit_new(
                sub,
                limit=max_posts,
                user_agent=settings.reddit_user_agent,
                client_id=settings.reddit_client_id,
                client_secret=settings.reddit_client_secret,
            )
            articles: list[RawArticle] = []
            for post in posts:
                fields = post_to_article(
                    post,
                    include_raw=include_raw,
                    content_max_chars=settings.reddit_content_max_chars,
                )
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
            logger.info("r/%s: fetched=%d saved=%d", sub, len(articles), saved)
        except Exception as exc:  # noqa: BLE001 — isolate per-subreddit failures
            logger.exception("Reddit ingestion failed for r/%s", sub)
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


def ingest_reddit_post_url(
    url: str,
    *,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    include_raw: bool = True,
) -> RawArticle | None:
    """Manually flag one Reddit post (regular or /s/ share URL) for ingest."""
    settings = get_settings()
    post = fetch_post_by_url(url, user_agent=settings.reddit_user_agent)
    if post is None:
        return None
    fields = post_to_article(
        post,
        include_raw=include_raw,
        content_max_chars=settings.reddit_content_max_chars,
    )
    if fields is None:
        return None
    article = RawArticle(**fields)
    article_store = store or JsonlArticleStore(store_path)
    refresh = getattr(article_store, "refresh", None)
    if callable(refresh):
        refresh([article])
    else:
        article_store.save([article])
    return article
