"""Ingest CourtListener search hits into the raw article store."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import httpx

from apps.aggregator.courtlistener import (
    SEARCH_TYPES,
    CourtListenerError,
    _search,
    parse_queries,
    parse_types,
)
from apps.aggregator.ingest_state import DEFAULT_STATE_PATH, JsonIngestState
from apps.aggregator.pipeline import DEFAULT_STORE_PATH
from apps.aggregator.storage import ArticleStore, JsonlArticleStore
from shared.schemas import IngestionRunResult, RawArticle, SourceIngestionResult
from shared.settings import get_settings

logger = logging.getLogger(__name__)


def _resolve_filed_after(
    *,
    search_type: str,
    since: str | None,
    use_watermark: bool,
    state: JsonIngestState,
    lookback_days: int,
) -> str | None:
    if since:
        return since
    if not use_watermark:
        return None
    stored = state.get(f"courtlistener:{search_type}")
    if not stored:
        return None
    try:
        watermark = date.fromisoformat(stored)
    except ValueError:
        logger.warning(
            "Ignoring unparseable CourtListener watermark %r for %s",
            stored,
            search_type,
        )
        return None
    return (watermark - timedelta(days=lookback_days)).isoformat()


def run_courtlistener_ingestion(
    *,
    token: str | None = None,
    queries: list[str] | None = None,
    types: list[str] | None = None,
    page_size: int | None = None,
    max_pages: int | None = None,
    since: str | None = None,
    use_watermark: bool = True,
    fetch_opinion_text: bool | None = None,
    state: JsonIngestState | None = None,
    state_path: str = DEFAULT_STATE_PATH,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    include_raw: bool = False,
) -> IngestionRunResult:
    """Pull RECAP dockets and/or case law opinions for insider-legal queries.

    Runs even without a token (anonymous rate limits apply). Prefer
    ``COURTLISTENER_API_TOKEN`` for production pulls. ``types`` defaults to
    ``COURTLISTENER_TYPES`` (dockets); explicit ``queries`` apply to all
    requested types.

    Incremental behavior: a per-type ``filed_after`` watermark (persisted in
    ``state_path``, minus ``COURTLISTENER_LOOKBACK_DAYS`` overlap) narrows
    re-runs; updated dockets are rewritten in place via the store's
    ``refresh`` (falling back to ``save`` for stores without it).
    """
    settings = get_settings()
    api_token = token if token is not None else settings.courtlistener_api_token
    type_list = parse_types(
        ",".join(types) if types is not None else settings.courtlistener_types
    )
    size = page_size if page_size is not None else settings.courtlistener_page_size
    pages = max_pages if max_pages is not None else settings.courtlistener_max_pages
    fetch_content = (
        fetch_opinion_text
        if fetch_opinion_text is not None
        else settings.courtlistener_fetch_opinion_text
    )
    content_max_chars = settings.courtlistener_opinion_text_max_chars
    lookback_days = settings.courtlistener_lookback_days
    ingest_state = state or JsonIngestState(state_path)

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
    run_day = started_at.date().isoformat()
    result = IngestionRunResult(started_at=started_at)
    article_store: ArticleStore = store or JsonlArticleStore(store_path)
    refresh = getattr(article_store, "refresh", None)

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

            filed_after = _resolve_filed_after(
                search_type=search_type,
                since=since,
                use_watermark=use_watermark,
                state=ingest_state,
                lookback_days=lookback_days,
            )
            if filed_after:
                logger.info(
                    "CourtListener %s incremental run: filed_after=%s",
                    search_type,
                    filed_after,
                )

            # Accumulate per link across queries so one store write happens
            # per type; otherwise the query line in each summary would make
            # the same case look "updated" on every overlapping query.
            collected: dict[str, RawArticle] = {}
            errors: list[str] = []
            for query in query_list:
                try:
                    articles = _search(
                        search_type=search_type,
                        query=query,
                        token=api_token,
                        page_size=size,
                        max_pages=pages,
                        filed_after=filed_after,
                        include_raw=include_raw,
                        fetch_content=fetch_content,
                        content_max_chars=content_max_chars,
                        client=client,
                    )
                    for article in articles:
                        collected.setdefault(article.link, article)
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

            fetched = len(collected)
            batch = list(collected.values())
            if callable(refresh):
                new, updated = refresh(batch)
                saved = new + updated
            else:
                saved = article_store.save(batch)

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
            if not errors and use_watermark:
                ingest_state.set(f"courtlistener:{search_type}", run_day)
            logger.info(
                "CourtListener %s ingestion complete: fetched=%d saved=%d errors=%d",
                search_type,
                fetched,
                saved,
                len(errors),
            )

    result.finished_at = datetime.now(UTC)
    return result
