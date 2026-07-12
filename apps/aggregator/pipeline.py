"""Ingestion pipeline: fetch → parse → store.

Designed as plain functions with clear Pydantic I/O so a future LangGraph
workflow can wrap each step as a node without rewriting the core logic.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from apps.aggregator.config import get_enabled_feeds
from apps.aggregator.fetcher import FeedFetchError, fetch_feed
from apps.aggregator.parser import FeedParseError, parse_feed
from apps.aggregator.storage import ArticleStore, JsonlArticleStore
from shared.schemas import (
    FeedSource,
    IngestionRunResult,
    SourceIngestionResult,
)

logger = logging.getLogger(__name__)

DEFAULT_STORE_PATH = "data/raw/articles.jsonl"


def ingest_source(
    source: FeedSource,
    store: ArticleStore,
    *,
    client: httpx.Client | None = None,
    include_raw: bool = False,
) -> SourceIngestionResult:
    """Ingest a single RSS source into the article store."""
    logger.info("Ingesting source %s (%s)", source.id, source.url)

    try:
        body = fetch_feed(str(source.url), client=client)
        articles = parse_feed(body, source, include_raw=include_raw)
        saved = store.save(articles)
        return SourceIngestionResult(
            source_id=source.id,
            source_name=source.name,
            success=True,
            articles_fetched=len(articles),
            articles_saved=saved,
        )
    except (FeedFetchError, FeedParseError) as exc:
        logger.error("Failed to ingest %s: %s", source.id, exc)
        return SourceIngestionResult(
            source_id=source.id,
            source_name=source.name,
            success=False,
            error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 — isolate per-source failures
        logger.exception("Unexpected error ingesting %s", source.id)
        return SourceIngestionResult(
            source_id=source.id,
            source_name=source.name,
            success=False,
            error=f"unexpected error: {exc}",
        )


def run_ingestion(
    sources: list[FeedSource] | None = None,
    *,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    include_raw: bool = False,
) -> IngestionRunResult:
    """Run ingestion for all enabled sources.

    Args:
        sources: Optional explicit list of feeds. Defaults to enabled DEFAULT_FEEDS.
        store: Optional ArticleStore. Defaults to JsonlArticleStore at store_path.
        store_path: Path used when store is not provided.
        include_raw: Attach a minimal raw entry payload to each article.
    """
    started_at = datetime.now(UTC)
    feeds = get_enabled_feeds(sources)
    article_store: ArticleStore = store or JsonlArticleStore(store_path)

    result = IngestionRunResult(started_at=started_at)

    if not feeds:
        logger.warning("No enabled feed sources to ingest")
        result.finished_at = datetime.now(UTC)
        return result

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for source in feeds:
            source_result = ingest_source(
                source,
                article_store,
                client=client,
                include_raw=include_raw,
            )
            result.sources.append(source_result)
            result.total_articles_saved += source_result.articles_saved

    result.finished_at = datetime.now(UTC)
    logger.info(
        "Ingestion complete: %d saved, %d source ok, %d source failed",
        result.total_articles_saved,
        result.success_count,
        result.failure_count,
    )
    return result
