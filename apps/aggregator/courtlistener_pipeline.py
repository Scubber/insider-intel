"""Ingest CourtListener search hits into the raw article store."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import httpx

from apps.aggregator.courtlistener import (
    SEARCH_TYPES,
    SOURCE_ID,
    CourtListenerError,
    _search,
    fetch_cluster_opinion_text,
    fetch_recap_document_text,
    parse_docket_id,
    parse_opinion_id,
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
    type_list = parse_types(",".join(types) if types is not None else settings.courtlistener_types)
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
                settings.courtlistener_opinion_queries or settings.courtlistener_queries
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


# Content that is only the ingest-time match tag, i.e. no document body yet.
_QUERY_TAG_PREFIX = "CourtListener query:"
_TEXT_ATTEMPT_KEY = "courtlistener_text:{link}"
_TEXT_RETRY_DAYS = 7


def needs_full_text(article: RawArticle) -> bool:
    """True when a CourtListener row has no document body in ``content``."""
    if not (article.source_id or "").startswith("courtlistener-"):
        return False
    content = (article.content or "").strip()
    if not content:
        return True
    # Ingest writes exactly one query-tag line; anything longer means a body
    # (opinion enricher / previous backfill) is already attached.
    return content.startswith(_QUERY_TAG_PREFIX) and "\n" not in content


def _attempted_recently(state: JsonIngestState, link: str, now: datetime) -> bool:
    stored = state.get(_TEXT_ATTEMPT_KEY.format(link=link))
    if not stored:
        return False
    try:
        attempted = datetime.fromisoformat(stored)
    except ValueError:
        return False
    if attempted.tzinfo is None:
        attempted = attempted.replace(tzinfo=UTC)
    return (now - attempted) < timedelta(days=_TEXT_RETRY_DAYS)


def _clear_llm_fields(processed_path: str, links: set[str]) -> None:
    """Drop paid-for LLM fields for links whose source text just changed.

    Without this, the summarize node's carry-forward would keep the thin
    pre-full-text case_record forever. Stripping ai_summary, case_record, and
    the ``source=="llm"`` ITM hits makes the next processing run re-extract
    over the full document (budget-bounded as usual).
    """
    from apps.aggregator.processed_storage import JsonlProcessedStore

    store = JsonlProcessedStore(processed_path)
    updated = []
    for row in store.load_all():
        if row.link not in links:
            continue
        if row.ai_summary is None and row.case_record is None:
            continue
        hits = [h for h in row.entities.itm_hits if getattr(h, "source", "lexical") != "llm"]
        updated.append(
            row.model_copy(
                update={
                    "ai_summary": None,
                    "case_record": None,
                    "entities": row.entities.model_copy(update={"itm_hits": hits}),
                }
            )
        )
    if updated:
        store.upsert(updated)


def run_courtlistener_text_backfill(
    *,
    token: str | None = None,
    limit: int | None = None,
    max_chars: int | None = None,
    state: JsonIngestState | None = None,
    state_path: str = DEFAULT_STATE_PATH,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    processed_path: str | None = None,
    client: httpx.Client | None = None,
) -> IngestionRunResult:
    """Pull full document text for stored CourtListener cases (free endpoints).

    Dockets read the RECAP archive's already-uploaded documents
    (``is_available=true`` — never a PACER purchase); opinion rows whose
    ingest-time enrich failed retry via the cluster detail. Enriched rows are
    force-refreshed in the raw store (fresh ``ingested_at`` → next
    ``run_processing`` re-scores and re-summarizes them over the full text),
    and their processed LLM fields are cleared so carry-forward cannot pin
    the thin pre-full-text extraction. No-text attempts are remembered and
    retried after 7 days (RECAP uploads trickle in).
    """
    settings = get_settings()
    api_token = token if token is not None else settings.courtlistener_api_token
    cap = max_chars if max_chars is not None else settings.courtlistener_recap_text_max_chars
    attempts_allowed = limit if limit is not None else settings.courtlistener_backfill_max_dockets
    ingest_state = state or JsonIngestState(state_path)
    article_store: ArticleStore = store or JsonlArticleStore(store_path)

    started_at = datetime.now(UTC)
    result = IngestionRunResult(started_at=started_at)
    fetched = 0
    saved = 0
    errors: list[str] = []

    candidates = [a for a in article_store.load_all() if needs_full_text(a)]
    candidates.sort(
        key=lambda a: (a.published or a.ingested_at).timestamp()
        if (a.published or a.ingested_at)
        else 0.0,
        reverse=True,
    )

    enriched: list[RawArticle] = []
    own_client = client is None
    http = client or httpx.Client(timeout=45.0, follow_redirects=True)
    try:
        attempts = 0
        for article in candidates:
            if attempts >= attempts_allowed:
                break
            if _attempted_recently(ingest_state, article.link, started_at):
                continue
            attempts += 1
            fetched += 1
            text = ""
            try:
                if article.source_id == SOURCE_ID:
                    docket_id = parse_docket_id(article.link)
                    if docket_id is not None:
                        text = fetch_recap_document_text(
                            docket_id, token=api_token, max_chars=cap, client=http
                        )
                else:
                    cluster_id = parse_opinion_id(article.link)
                    if cluster_id is not None:
                        text = (
                            fetch_cluster_opinion_text(
                                cluster_id,
                                token=api_token,
                                max_chars=cap,
                                client=http,
                            )
                            or ""
                        )
            except CourtListenerError as exc:
                logger.warning("Text backfill failed for %s: %s", article.link, exc)
                errors.append(f"{article.link}: {exc}")

            ingest_state.set(_TEXT_ATTEMPT_KEY.format(link=article.link), started_at.isoformat())
            if not text.strip():
                continue
            base = (article.content or "").strip()
            combined = f"{base}\n{text.strip()}" if base else text.strip()
            enriched.append(
                article.model_copy(update={"content": combined, "ingested_at": datetime.now(UTC)})
            )
    finally:
        if own_client:
            http.close()

    if enriched:
        refresh = getattr(article_store, "refresh", None)
        if callable(refresh):
            new, updated = refresh(enriched, force=True)
            saved = new + updated
        else:
            saved = article_store.save(enriched)
        _clear_llm_fields(
            processed_path or "data/processed/articles.jsonl",
            {a.link for a in enriched},
        )

    result.sources.append(
        SourceIngestionResult(
            source_id="courtlistener-fulltext",
            source_name="CourtListener full text",
            success=not (errors and saved == 0),
            articles_fetched=fetched,
            articles_saved=saved,
            error="; ".join(errors[:5]) if errors and saved == 0 else None,
        )
    )
    result.total_articles_saved = saved
    result.finished_at = datetime.now(UTC)
    logger.info(
        "CourtListener text backfill: attempted=%d enriched=%d errors=%d",
        fetched,
        saved,
        len(errors),
    )
    return result
