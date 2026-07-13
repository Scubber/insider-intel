"""Ingest CourtListener search hits into the raw article store."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from apps.aggregator.courtlistener import (
    SEARCH_TYPES,
    CourtListenerError,
    _search,
    parse_queries,
    parse_types,
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
    types: list[str] | None = None,
    page_size: int | None = None,
    max_pages: int | None = None,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    include_raw: bool = False,
) -> IngestionRunResult:
    """Pull RECAP dockets and/or case law opinions for insider-legal queries.

    Runs even without a token (anonymous rate limits apply). Prefer
    ``COURTLISTENER_API_TOKEN`` for production pulls. ``types`` defaults to
    ``COURTLISTENER_TYPES`` (dockets); explicit ``queries`` apply to all
    requested types.
    """
    settings = get_settings()
    api_token = token if token is not None else settings.courtlistener_api_token
    type_list = parse_types(
        ",".join(types) if types is not None else settings.courtlistener_types
    )
    size = page_size if page_size is not None else settings.courtlistener_page_size
    pages = max_pages if max_pages is not None else settings.courtlistener_max_pages

    def queries_for(search_type: str) -> list[str]:
        if queries is not None:
            return queries
        if search_type == "opinions":
            return parse_queries(
                settings.courtlistener_opinion_queries
                or settings.courtlistener_queries
            )
        return parse_queries(settings.courtlistener_queries)

    started_at = datetime.now(UTC)
    result = IngestionRunResult(started_at=started_at)
    article_store: ArticleStore = store or JsonlArticleStore(store_path)

    with httpx.Client(timeout=45.0, follow_redirects=True) as client:
        for search_type in type_list:
            spec = SEARCH_TYPES[search_type]
            query_list = queries_for(search_type)
            if not query_list:
                logger.info(
                    "CourtListener %s ingest skipped (no queries configured)",
                    search_type,
                )
                continue

            fetched = 0
            saved = 0
            errors: list[str] = []
            for query in query_list:
                try:
                    articles = _search(
                        search_type=search_type,
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
                    logger.error(
                        "CourtListener %s query failed %r: %s",
                        search_type,
                        query,
                        exc,
                    )
                    errors.append(f"{query}: {exc}")
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "Unexpected CourtListener %s error for %r",
                        search_type,
                        query,
                    )
                    errors.append(f"{query}: unexpected error: {exc}")

            result.sources.append(
                SourceIngestionResult(
                    source_id=spec.source_id,
                    source_name=spec.source_name,
                    success=not (errors and fetched == 0),
                    articles_fetched=fetched,
                    articles_saved=saved,
                    error="; ".join(errors) if errors and fetched == 0 else None,
                )
            )
            result.total_articles_saved += saved
            logger.info(
                "CourtListener %s ingestion complete: fetched=%d saved=%d errors=%d",
                search_type,
                fetched,
                saved,
                len(errors),
            )

    result.finished_at = datetime.now(UTC)
    return result
