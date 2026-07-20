"""Ingest curated long-form publications (and single URLs) into raw storage."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from urllib.parse import urlparse

from apps.aggregator.pipeline import DEFAULT_STORE_PATH
from apps.aggregator.publication_extract import PublicationDoc, fetch_publication
from apps.aggregator.publication_sources import (
    PublicationSource,
    get_publication_sources,
)
from apps.aggregator.storage import ArticleStore, JsonlArticleStore
from shared.schemas import IngestionRunResult, RawArticle, SourceIngestionResult
from shared.settings import get_settings

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _doc_to_article(
    doc: PublicationDoc,
    *,
    link: str,
    source_id: str,
    source_name: str,
    include_raw: bool,
) -> RawArticle:
    return RawArticle(
        title=doc.title or source_name,
        link=link,
        published=doc.published,
        summary=doc.summary,
        content=doc.content,
        source_id=source_id,
        source_name=source_name,
        channel="publications",
        raw={"pdf_url": doc.pdf_url} if include_raw and doc.pdf_url else None,
    )


def _save(article_store: ArticleStore, articles: list[RawArticle]) -> int:
    refresh = getattr(article_store, "refresh", None)
    if callable(refresh):
        new_count, updated = refresh(articles)
        return new_count + updated
    return article_store.save(articles)


def run_publications_ingestion(
    *,
    source_ids: list[str] | None = None,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    include_raw: bool = False,
) -> IngestionRunResult:
    """Sweep the curated publications catalog into JSONL storage.

    Uses refresh() so a sweep that later gains PDF text upgrades a stored
    metadata-only row.
    """
    settings = get_settings()
    started = datetime.now(UTC)
    article_store = store or JsonlArticleStore(store_path)
    catalog: list[PublicationSource] = get_publication_sources(source_ids)

    sources: list[SourceIngestionResult] = []
    total_saved = 0
    for source in catalog:
        try:
            doc = fetch_publication(
                source.url,
                pdf_url=source.pdf_url,
                user_agent=settings.publications_user_agent,
                content_max_chars=settings.publications_content_max_chars,
                pdf_max_bytes=settings.publications_pdf_max_bytes,
            )
            article = _doc_to_article(
                doc,
                link=source.url,
                source_id=source.id,
                source_name=source.name,
                include_raw=include_raw,
            )
            saved = _save(article_store, [article])
            total_saved += saved
            sources.append(
                SourceIngestionResult(
                    source_id=source.id,
                    source_name=source.name,
                    success=True,
                    articles_fetched=1,
                    articles_saved=saved,
                )
            )
            logger.info(
                "%s: saved=%d content_chars=%d pdf=%s",
                source.id,
                saved,
                len(doc.content or ""),
                doc.pdf_url or "-",
            )
        except Exception as exc:  # noqa: BLE001 — isolate per-source failures
            logger.exception("Publication ingestion failed for %s", source.id)
            sources.append(
                SourceIngestionResult(
                    source_id=source.id,
                    source_name=source.name,
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


def _adhoc_source(url: str) -> tuple[str, str]:
    host = (urlparse(url).netloc or "publication").lower().removeprefix("www.")
    slug = _SLUG_RE.sub("-", host).strip("-") or "adhoc"
    return f"pub-{slug}", host


def ingest_publication_url(
    url: str,
    *,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    include_raw: bool = True,
) -> RawArticle | None:
    """Manually flag one publication URL (landing page or direct PDF)."""
    settings = get_settings()
    doc = fetch_publication(
        url,
        user_agent=settings.publications_user_agent,
        content_max_chars=settings.publications_content_max_chars,
        pdf_max_bytes=settings.publications_pdf_max_bytes,
    )
    if not doc.title and not doc.content:
        return None
    source_id, host = _adhoc_source(url)
    article = _doc_to_article(
        doc,
        link=url,
        source_id=source_id,
        source_name=host,
        include_raw=include_raw,
    )
    article_store = store or JsonlArticleStore(store_path)
    _save(article_store, [article])
    return article
