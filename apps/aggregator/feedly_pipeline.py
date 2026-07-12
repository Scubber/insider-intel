"""Ingest articles from configured Feedly boards / AI Feeds / folders."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from apps.aggregator.feedly import FeedlyError, pull_stream_articles
from apps.aggregator.pipeline import DEFAULT_STORE_PATH
from apps.aggregator.storage import ArticleStore, JsonlArticleStore
from shared.schemas import IngestionRunResult, SourceIngestionResult
from shared.settings import get_settings

logger = logging.getLogger(__name__)


def run_feedly_ingestion(
    *,
    access_token: str | None = None,
    stream_ids: list[str] | None = None,
    count: int | None = None,
    max_pages: int | None = None,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    include_raw: bool = False,
) -> IngestionRunResult:
    """Pull Feedly streams into the raw article store.

    Skips cleanly (zero sources) when token / stream ids are not configured.
    """
    settings = get_settings()
    token = (access_token if access_token is not None else settings.feedly_access_token) or ""
    streams = stream_ids if stream_ids is not None else settings.feedly_stream_id_list()
    page_count = count if count is not None else settings.feedly_count
    pages = max_pages if max_pages is not None else settings.feedly_max_pages

    started_at = datetime.now(UTC)
    result = IngestionRunResult(started_at=started_at)
    article_store: ArticleStore = store or JsonlArticleStore(store_path)

    if not token.strip() or not streams:
        logger.info(
            "Feedly ingest skipped (set FEEDLY_ACCESS_TOKEN and FEEDLY_STREAM_IDS to enable)"
        )
        result.finished_at = datetime.now(UTC)
        return result

    with httpx.Client(timeout=45.0, follow_redirects=True) as client:
        for stream_id in streams:
            source_id = f"feedly:{stream_id}"
            try:
                articles = pull_stream_articles(
                    access_token=token,
                    stream_id=stream_id,
                    count=page_count,
                    max_pages=pages,
                    include_raw=include_raw,
                    client=client,
                )
                saved = article_store.save(articles)
                name = articles[0].source_name if articles else f"Feedly {stream_id}"
                result.sources.append(
                    SourceIngestionResult(
                        source_id=source_id,
                        source_name=name,
                        success=True,
                        articles_fetched=len(articles),
                        articles_saved=saved,
                    )
                )
                result.total_articles_saved += saved
            except FeedlyError as exc:
                logger.error("Feedly ingest failed for %s: %s", stream_id, exc)
                result.sources.append(
                    SourceIngestionResult(
                        source_id=source_id,
                        source_name=f"Feedly {stream_id}",
                        success=False,
                        error=str(exc),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unexpected Feedly error for %s", stream_id)
                result.sources.append(
                    SourceIngestionResult(
                        source_id=source_id,
                        source_name=f"Feedly {stream_id}",
                        success=False,
                        error=f"unexpected error: {exc}",
                    )
                )

    result.finished_at = datetime.now(UTC)
    logger.info(
        "Feedly ingestion complete: saved=%d streams_ok=%d streams_failed=%d",
        result.total_articles_saved,
        result.success_count,
        result.failure_count,
    )
    return result
