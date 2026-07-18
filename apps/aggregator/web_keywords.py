"""Web keyword discovery via alert RSS feeds (Google Alerts style).

Configure ``WEB_KEYWORD_FEED_URLS`` with comma-separated RSS URLs.
No paid search API required for MVP.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import feedparser
import httpx

from apps.aggregator.pipeline import DEFAULT_STORE_PATH
from apps.aggregator.storage import ArticleStore, JsonlArticleStore
from shared.schemas import IngestionRunResult, RawArticle, SourceIngestionResult
from shared.settings import get_settings

logger = logging.getLogger(__name__)

SOURCE_ID = "web-keyword"
SOURCE_NAME = "Web keyword alerts"
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(value: str | None) -> str:
    if not value:
        return ""
    return _TAG_RE.sub(" ", value).replace("&nbsp;", " ").strip()


def _entry_published(entry: Any) -> datetime | None:
    for key in ("published", "updated"):
        raw = getattr(entry, key, None) or (entry.get(key) if isinstance(entry, dict) else None)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(str(raw))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
    return None


def entry_to_raw_article(
    entry: Any,
    *,
    feed_url: str,
    include_raw: bool = False,
) -> RawArticle | None:
    title = _strip_html(getattr(entry, "title", None) or "")
    link = (getattr(entry, "link", None) or "").strip()
    if not title or not link.startswith("http"):
        return None
    summary = _strip_html(getattr(entry, "summary", None) or getattr(entry, "description", None))
    host = urlparse(feed_url).netloc or "alert-rss"
    note = f"Alert feed: {host}"
    body = f"{summary}\n\n{note}".strip() if summary else note
    raw_payload: dict[str, Any] | None = None
    if include_raw:
        raw_payload = {
            "title": getattr(entry, "title", None),
            "link": link,
            "summary": getattr(entry, "summary", None),
            "feed_url": feed_url,
        }
    return RawArticle(
        title=title,
        link=link,
        published=_entry_published(entry),
        summary=body,
        source_id=SOURCE_ID,
        source_name=SOURCE_NAME,
        channel="news",
        raw=raw_payload,
    )


def pull_alert_feed(
    feed_url: str,
    *,
    include_raw: bool = False,
    client: httpx.Client | None = None,
) -> list[RawArticle]:
    """Fetch one alert RSS/Atom feed and map entries to RawArticle."""
    own_client = client is None
    http = client or httpx.Client(timeout=45.0, follow_redirects=True)
    try:
        resp = http.get(feed_url)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"failed fetching {feed_url}: {exc}") from exc
    finally:
        if own_client:
            http.close()

    articles: list[RawArticle] = []
    seen: set[str] = set()
    for entry in parsed.entries or []:
        article = entry_to_raw_article(entry, feed_url=feed_url, include_raw=include_raw)
        if article is None or article.link in seen:
            continue
        seen.add(article.link)
        articles.append(article)
    return articles


def run_web_keyword_ingestion(
    *,
    feed_urls: list[str] | None = None,
    store: ArticleStore | None = None,
    store_path: str = DEFAULT_STORE_PATH,
    include_raw: bool = False,
) -> IngestionRunResult:
    """Ingest configured alert RSS feeds. Skips when no URLs configured."""
    settings = get_settings()
    urls = feed_urls if feed_urls is not None else settings.web_keyword_feed_url_list()

    started_at = datetime.now(UTC)
    result = IngestionRunResult(started_at=started_at)
    article_store: ArticleStore = store or JsonlArticleStore(store_path)

    if not urls:
        logger.info("Web keyword ingest skipped (set WEB_KEYWORD_FEED_URLS to enable)")
        result.finished_at = datetime.now(UTC)
        return result

    with httpx.Client(timeout=45.0, follow_redirects=True) as client:
        for feed_url in urls:
            source_key = f"web-keyword:{urlparse(feed_url).netloc or 'feed'}"
            try:
                articles = pull_alert_feed(
                    feed_url,
                    include_raw=include_raw,
                    client=client,
                )
                saved = article_store.save(articles)
                result.sources.append(
                    SourceIngestionResult(
                        source_id=source_key,
                        source_name=SOURCE_NAME,
                        success=True,
                        articles_fetched=len(articles),
                        articles_saved=saved,
                    )
                )
                result.total_articles_saved += saved
            except Exception as exc:  # noqa: BLE001
                logger.error("Web keyword feed failed %s: %s", feed_url, exc)
                result.sources.append(
                    SourceIngestionResult(
                        source_id=source_key,
                        source_name=SOURCE_NAME,
                        success=False,
                        error=str(exc),
                    )
                )

    result.finished_at = datetime.now(UTC)
    logger.info(
        "Web keyword ingestion complete: saved=%d feeds_ok=%d feeds_failed=%d",
        result.total_articles_saved,
        result.success_count,
        result.failure_count,
    )
    return result
