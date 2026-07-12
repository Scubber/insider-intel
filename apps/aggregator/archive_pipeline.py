"""Sitemap keyword archive backfill → RawArticle store."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

import httpx

from apps.aggregator.archive_sources import (
    DEFAULT_ARCHIVE_KEYWORDS,
    ArchiveSource,
    get_archive_sources,
)
from apps.aggregator.html_extract import (
    HtmlExtractError,
    fetch_and_extract,
    text_matches_keywords,
)
from apps.aggregator.pipeline import DEFAULT_STORE_PATH
from apps.aggregator.sitemap import (
    SitemapError,
    collect_sitemap_urls,
    url_matches_keywords,
)
from apps.aggregator.storage import ArticleStore, JsonlArticleStore
from shared.schemas import IngestionRunResult, RawArticle, SourceIngestionResult

logger = logging.getLogger(__name__)


def _keywords_match_url_or_text(
    url: str,
    title: str,
    summary: str | None,
    keywords: tuple[str, ...] | list[str],
) -> bool:
    if url_matches_keywords(url, keywords):
        return True
    blob = f"{title}\n{summary or ''}"
    return text_matches_keywords(blob, keywords)


def ingest_archive_source(
    source: ArchiveSource,
    store: ArticleStore,
    *,
    keywords: tuple[str, ...] | list[str] = DEFAULT_ARCHIVE_KEYWORDS,
    max_urls: int = 200,
    max_sitemaps: int = 40,
    delay_seconds: float = 1.0,
    include_raw: bool = False,
    client: httpx.Client | None = None,
) -> SourceIngestionResult:
    """Discover sitemap URLs, keyword-filter, fetch HTML, save RawArticles."""
    owns = client is None
    http = client or httpx.Client(timeout=45.0, follow_redirects=True)
    try:
        try:
            all_urls = collect_sitemap_urls(
                source.sitemap_url,
                client=http,
                max_sitemaps=max_sitemaps,
                max_urls=max(max_urls * 20, 5_000),
                delay_seconds=min(delay_seconds, 0.5),
                path_hints=source.url_path_hints,
                child_hints=source.sitemap_child_hints,
                skip_sitemap_substrings=source.skip_sitemap_substrings,
            )
        except SitemapError as exc:
            return SourceIngestionResult(
                source_id=source.id,
                source_name=source.name,
                success=False,
                error=str(exc),
            )

        # Prefer URL-keyword hits first, then fill remaining budget by scanning.
        url_hits = [u for u in all_urls if url_matches_keywords(u, keywords)]
        hit_set = set(url_hits)
        others = [u for u in all_urls if u not in hit_set]
        candidates = (url_hits + others)[: max(0, max_urls)]

        articles: list[RawArticle] = []
        for i, url in enumerate(candidates):
            try:
                extracted = fetch_and_extract(
                    url, client=http, include_raw=include_raw
                )
            except HtmlExtractError as exc:
                logger.debug("Skip %s: %s", url, exc)
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue

            if not _keywords_match_url_or_text(
                url, extracted.title, extracted.summary, keywords
            ):
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue

            articles.append(
                RawArticle(
                    title=extracted.title,
                    link=url,
                    published=extracted.published,
                    summary=extracted.summary,
                    source_id=source.id,
                    source_name=source.name,
                    channel=source.channel,
                    raw=extracted.raw_meta if include_raw else None,
                )
            )
            if delay_seconds > 0 and i + 1 < len(candidates):
                time.sleep(delay_seconds)

        fetched = len(articles)
        saved = store.save(articles) if articles else 0
        return SourceIngestionResult(
            source_id=source.id,
            source_name=source.name,
            success=True,
            articles_fetched=fetched,
            articles_saved=saved,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Archive ingest failed for %s", source.id)
        return SourceIngestionResult(
            source_id=source.id,
            source_name=source.name,
            success=False,
            error=f"unexpected error: {exc}",
        )
    finally:
        if owns:
            http.close()


def run_archive_ingestion(
    *,
    source_ids: list[str] | None = None,
    keywords: list[str] | None = None,
    max_urls: int = 200,
    max_sitemaps: int = 40,
    delay_seconds: float = 1.0,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    include_raw: bool = False,
) -> IngestionRunResult:
    """Run keyword-filtered sitemap archive backfill for configured sources."""
    started_at = datetime.now(UTC)
    result = IngestionRunResult(started_at=started_at)
    article_store: ArticleStore = store or JsonlArticleStore(store_path)
    sources = get_archive_sources(source_ids)
    kws: tuple[str, ...] = tuple(keywords) if keywords else DEFAULT_ARCHIVE_KEYWORDS

    if not sources:
        logger.warning("No archive sources selected")
        result.finished_at = datetime.now(UTC)
        return result

    with httpx.Client(timeout=45.0, follow_redirects=True) as client:
        for source in sources:
            logger.info("Archive ingest: %s (%s)", source.id, source.sitemap_url)
            source_result = ingest_archive_source(
                source,
                article_store,
                keywords=kws,
                max_urls=max_urls,
                max_sitemaps=max_sitemaps,
                delay_seconds=delay_seconds,
                include_raw=include_raw,
                client=client,
            )
            result.sources.append(source_result)
            result.total_articles_saved += source_result.articles_saved

    result.finished_at = datetime.now(UTC)
    logger.info(
        "Archive ingestion complete: saved=%d sources ok=%d failed=%d",
        result.total_articles_saved,
        result.success_count,
        result.failure_count,
    )
    return result
