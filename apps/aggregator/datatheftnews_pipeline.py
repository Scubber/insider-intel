"""Ingest DataTheftNews published posts into the raw article store."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from apps.aggregator.datatheftnews import (
    DEFAULT_SUPABASE_URL,
    SOURCE_ID,
    SOURCE_NAME,
    fetch_published_posts,
    resolve_anon_key,
    row_to_article,
)
from apps.aggregator.pipeline import DEFAULT_STORE_PATH
from apps.aggregator.storage import ArticleStore, JsonlArticleStore
from shared.schemas import IngestionRunResult, RawArticle, SourceIngestionResult
from shared.settings import get_settings

logger = logging.getLogger(__name__)


def run_datatheftnews_ingestion(
    *,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    include_raw: bool = False,
    limit: int | None = None,
    supabase_url: str | None = None,
    anon_key: str | None = None,
) -> IngestionRunResult:
    """Pull published DataTheftNews posts (Supabase) into JSONL storage."""
    settings = get_settings()
    started = datetime.now(UTC)
    article_store = store or JsonlArticleStore(store_path)
    max_posts = limit if limit is not None else settings.datatheftnews_limit
    url = (supabase_url or settings.datatheftnews_supabase_url or DEFAULT_SUPABASE_URL).rstrip(
        "/"
    )

    try:
        key = resolve_anon_key(anon_key or settings.datatheftnews_anon_key)
        rows = fetch_published_posts(
            supabase_url=url,
            anon_key=key,
            limit=max_posts,
        )
        articles: list[RawArticle] = []
        for row in rows:
            fields = row_to_article(
                row,
                include_raw=include_raw,
                content_max_chars=settings.datatheftnews_content_max_chars,
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

        source = SourceIngestionResult(
            source_id=SOURCE_ID,
            source_name=SOURCE_NAME,
            success=True,
            articles_fetched=len(articles),
            articles_saved=saved,
        )
        logger.info(
            "DataTheftNews ingestion complete: fetched=%d saved=%d",
            len(articles),
            saved,
        )
    except Exception as exc:  # noqa: BLE001 — surface as source failure
        logger.exception("DataTheftNews ingestion failed")
        source = SourceIngestionResult(
            source_id=SOURCE_ID,
            source_name=SOURCE_NAME,
            success=False,
            error=str(exc),
        )

    return IngestionRunResult(
        started_at=started,
        finished_at=datetime.now(UTC),
        sources=[source],
        total_articles_saved=source.articles_saved if source.success else 0,
    )
