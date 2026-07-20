"""Ingest curated long-form publications (and single URLs) into raw storage."""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from apps.aggregator.pipeline import DEFAULT_STORE_PATH
from apps.aggregator.process_pipeline import DEFAULT_PROCESSED_PATH
from apps.aggregator.publication_extract import (
    PublicationDoc,
    fetch_publication,
)
from apps.aggregator.publication_sources import (
    PublicationSource,
    get_publication_sources,
)
from apps.aggregator.storage import ArticleStore, JsonlArticleStore
from shared.schemas import IngestionRunResult, RawArticle, SourceIngestionResult
from shared.settings import get_settings

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")

# A refetch counts as "substantially better" (PDF text vs page text) when it
# is both much longer relatively and meaningfully long absolutely.
_UPGRADE_MIN_CHARS = 5_000
_UPGRADE_MIN_RATIO = 2.0
_COLLECTION_ITEM_DELAY_SECONDS = 1.0


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


def _save_with_upgrade(
    article_store: ArticleStore,
    articles: list[RawArticle],
    *,
    stored_by_link: dict[str, RawArticle],
    processed_path: str | Path,
) -> int:
    """Save via refresh, force-replacing rows whose text got substantially better.

    The recurring sweep is the retry path for PDF extraction: a run that gets
    full PDF text where a prior run only got landing-page text force-updates
    the stored row and clears its paid-for LLM fields so the next processing
    pass re-enriches over the full document (CourtListener backfill precedent).
    """
    refresh = getattr(article_store, "refresh", None)
    if not callable(refresh):
        return article_store.save(articles)

    upgrades: list[RawArticle] = []
    normal: list[RawArticle] = []
    for article in articles:
        prior = stored_by_link.get(article.link)
        new_len = len(article.content or "")
        old_len = len((prior.content if prior else None) or "")
        if (
            prior is not None
            and old_len > 0
            and new_len >= _UPGRADE_MIN_CHARS
            and new_len >= old_len * _UPGRADE_MIN_RATIO
        ):
            upgrades.append(article)
        else:
            normal.append(article)

    saved = 0
    if normal:
        new_count, updated = refresh(normal)
        saved += new_count + updated
    if upgrades:
        new_count, updated = refresh(upgrades, force=True)
        saved += new_count + updated
        links = {a.link for a in upgrades}
        from apps.aggregator.courtlistener_pipeline import _clear_llm_fields

        _clear_llm_fields(str(processed_path), links)
        logger.info(
            "Upgraded %d publication(s) with substantially longer text: %s",
            len(upgrades),
            ", ".join(sorted(links)),
        )
    return saved


def _fetch_source_articles(
    source: PublicationSource,
    *,
    settings,
    client: httpx.Client,
    include_raw: bool,
) -> list[RawArticle]:
    """One article for a document source; one per item page for a collection."""
    fetch_kwargs = {
        "client": client,
        "user_agent": settings.publications_user_agent,
        "content_max_chars": settings.publications_content_max_chars,
        "pdf_max_bytes": settings.publications_pdf_max_bytes,
    }
    if source.kind != "collection":
        doc = fetch_publication(source.url, pdf_url=source.pdf_url, **fetch_kwargs)
        return [
            _doc_to_article(
                doc,
                link=source.url,
                source_id=source.id,
                source_name=source.name,
                include_raw=include_raw,
            )
        ]

    from apps.aggregator.publication_extract import fetch_collection_items

    items = fetch_collection_items(
        source.url,
        client=client,
        user_agent=settings.publications_user_agent,
    )[: source.max_items]
    if not items:
        logger.info("%s: collection page yielded no item links", source.id)
    articles: list[RawArticle] = []
    for position, item_url in enumerate(items):
        if position > 0:
            time.sleep(_COLLECTION_ITEM_DELAY_SECONDS)
        try:
            doc = fetch_publication(item_url, **fetch_kwargs)
        except Exception as exc:  # noqa: BLE001 — item failures don't sink the collection
            logger.warning("%s: item %s failed: %s", source.id, item_url, exc)
            continue
        articles.append(
            _doc_to_article(
                doc,
                link=item_url,
                source_id=source.id,
                source_name=source.name,
                include_raw=include_raw,
            )
        )
    return articles


def run_publications_ingestion(
    *,
    source_ids: list[str] | None = None,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    processed_path: str | Path = DEFAULT_PROCESSED_PATH,
    include_raw: bool = False,
) -> IngestionRunResult:
    """Sweep the curated publications catalog into JSONL storage.

    The recurring sweep doubles as the PDF retry path: rows whose refetched
    text is substantially longer (PDF extracted where a prior run fell back to
    page text) are force-updated and their stale LLM fields cleared.
    """
    settings = get_settings()
    started = datetime.now(UTC)
    article_store = store or JsonlArticleStore(store_path)
    catalog: list[PublicationSource] = get_publication_sources(source_ids)

    stored_by_link: dict[str, RawArticle] = {}
    load_all = getattr(article_store, "load_all", None)
    if callable(load_all):
        stored_by_link = {a.link: a for a in load_all() if (a.source_id or "").startswith("pub-")}

    sources: list[SourceIngestionResult] = []
    total_saved = 0
    with httpx.Client(timeout=45.0, follow_redirects=True) as client:
        for source in catalog:
            try:
                articles = _fetch_source_articles(
                    source,
                    settings=settings,
                    client=client,
                    include_raw=include_raw,
                )
                saved = _save_with_upgrade(
                    article_store,
                    articles,
                    stored_by_link=stored_by_link,
                    processed_path=processed_path,
                )
                total_saved += saved
                sources.append(
                    SourceIngestionResult(
                        source_id=source.id,
                        source_name=source.name,
                        success=True,
                        articles_fetched=len(articles),
                        articles_saved=saved,
                    )
                )
                logger.info(
                    "%s: fetched=%d saved=%d content_chars=%s",
                    source.id,
                    len(articles),
                    saved,
                    ",".join(str(len(a.content or "")) for a in articles) or "-",
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
