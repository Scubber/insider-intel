"""Ingest CourtListener RECAP search hits into the raw article store."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from apps.aggregator.courtlistener import (
    SOURCE_ID,
    SOURCE_NAME,
    CourtListenerError,
    parse_queries,
    search_recap,
)
from apps.aggregator.pipeline import DEFAULT_STORE_PATH
from apps.aggregator.storage import ArticleStore, JsonlArticleStore
from shared.schemas import IngestionRunResult, SourceIngestionResult
from shared.settings import get_settings

logger = logging.getLogger(__name__)


def run_courtlistener_ingestion(
    *,
    token: str | None = None,
    queries: list[str] | None = None,
    page_size: int | None = None,
    max_pages: int | None = None,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    include_raw: bool = False,
) -> IngestionRunResult:
    """Pull RECAP dockets for curated insider-legal queries.

    Runs even without a token (anonymous rate limits apply). Prefer
    ``COURTLISTENER_API_TOKEN`` for production pulls.
    """
    settings = get_settings()
    api_token = token if token is not None else settings.courtlistener_api_token
    query_list = (
        queries
        if queries is not None
        else parse_queries(settings.courtlistener_queries)
    )
    size = page_size if page_size is not None else settings.courtlistener_page_size
    pages = max_pages if max_pages is not None else settings.courtlistener_max_pages

    started_at = datetime.now(UTC)
    result = IngestionRunResult(started_at=started_at)
    article_store: ArticleStore = store or JsonlArticleStore(store_path)

    if not query_list:
        logger.info("CourtListener ingest skipped (no queries configured)")
        result.finished_at = datetime.now(UTC)
        return result

    fetched = 0
    saved = 0
    errors: list[str] = []

    with httpx.Client(timeout=45.0, follow_redirects=True) as client:
        for query in query_list:
            try:
                articles = search_recap(
                    query=query,
                    token=api_token,
                    page_size=size,
                    max_pages=pages,
                    include_raw=include_raw,
                    client=client,
                )
                fetched += len(articles)
                saved += article_store.save(articles)
            except CourtListenerError as exc:
                logger.error("CourtListener query failed %r: %s", query, exc)
                errors.append(f"{query}: {exc}")
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unexpected CourtListener error for %r", query)
                errors.append(f"{query}: unexpected error: {exc}")

    ok = not errors or saved > 0 or fetched > 0
    result.sources.append(
        SourceIngestionResult(
            source_id=SOURCE_ID,
            source_name=SOURCE_NAME,
            success=ok and not (errors and fetched == 0),
            articles_fetched=fetched,
            articles_saved=saved,
            error="; ".join(errors) if errors and fetched == 0 else None,
        )
    )
    result.total_articles_saved += saved
    result.finished_at = datetime.now(UTC)
    logger.info(
        "CourtListener ingestion complete: fetched=%d saved=%d errors=%d",
        fetched,
        saved,
        len(errors),
    )
    return result
