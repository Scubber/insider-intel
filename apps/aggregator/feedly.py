"""Feedly Cloud API client — pull board / AI Feed / folder streams as RawArticle.

Requires a Feedly developer / Teams API token. Stream IDs come from the Feedly UI
(board/folder share panel or API). Example boards from insider OSINT workflows:
  - Insider Threats x Top Stories
  - ITM-Hunt
  - Insider Threats x Novel TTPs

Docs: https://developers.feedly.com/reference/collect-articles
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from shared.schemas import RawArticle

logger = logging.getLogger(__name__)

FEEDLY_STREAMS_URL = "https://cloud.feedly.com/v3/streams/contents"
_TAG_RE = re.compile(r"<[^>]+>")


class FeedlyError(RuntimeError):
    """Feedly API request or parse failure."""


def _strip_html(value: str | None) -> str:
    if not value:
        return ""
    return _TAG_RE.sub(" ", value).replace("&nbsp;", " ").strip()


def _ms_to_datetime(ms: int | float | None) -> datetime | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0, tz=UTC)
    except (OverflowError, OSError, ValueError, TypeError):
        return None


def _entry_link(entry: dict[str, Any]) -> str | None:
    for key in ("canonicalUrl", "ampUrl", "originId"):
        value = entry.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    alternate = entry.get("alternate")
    if isinstance(alternate, list):
        for item in alternate:
            if isinstance(item, dict):
                href = item.get("href")
                if isinstance(href, str) and href.startswith("http"):
                    return href
    return None


def _entry_summary(entry: dict[str, Any]) -> str | None:
    parts: list[str] = []
    for key in ("summary", "content"):
        block = entry.get(key)
        if isinstance(block, dict):
            text = _strip_html(block.get("content"))
            if text:
                parts.append(text)
        elif isinstance(block, str):
            text = _strip_html(block)
            if text:
                parts.append(text)

    labels: list[str] = []
    for cat in entry.get("categories") or []:
        if isinstance(cat, dict) and cat.get("label"):
            labels.append(str(cat["label"]))
    for kw in entry.get("keywords") or []:
        if isinstance(kw, str) and kw.strip():
            labels.append(kw.strip())
    if labels:
        parts.append("Feedly labels: " + "; ".join(dict.fromkeys(labels)))

    if not parts:
        return None
    return "\n\n".join(parts)


def _source_id_for_stream(stream_id: str) -> str:
    # Keep stable, filesystem-friendly ids
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", stream_id).strip("-").lower()
    return f"feedly-{cleaned[:80] or 'stream'}"


def entry_to_raw_article(
    entry: dict[str, Any],
    *,
    stream_id: str,
    stream_label: str | None = None,
    include_raw: bool = False,
) -> RawArticle | None:
    """Map one Feedly stream entry to RawArticle."""
    title = (entry.get("title") or "").strip()
    link = _entry_link(entry)
    if not title or not link:
        return None

    origin = entry.get("origin") if isinstance(entry.get("origin"), dict) else {}
    origin_title = (origin.get("title") or stream_label or "Feedly").strip()
    source_id = _source_id_for_stream(stream_id)

    return RawArticle(
        title=title,
        link=link,
        published=_ms_to_datetime(entry.get("published") or entry.get("crawled")),
        summary=_entry_summary(entry),
        source_id=source_id,
        source_name=f"Feedly · {origin_title}",
        channel="news",
        raw=entry if include_raw else None,
    )


def fetch_stream_entries(
    *,
    access_token: str,
    stream_id: str,
    count: int = 50,
    continuation: str | None = None,
    client: httpx.Client | None = None,
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """Fetch one page of Feedly stream contents.

    Returns (items, continuation, stream_title).
    """
    if not access_token.strip():
        raise FeedlyError("FEEDLY_ACCESS_TOKEN is empty")
    if not stream_id.strip():
        raise FeedlyError("stream_id is empty")

    params: dict[str, str | int] = {
        "streamId": stream_id,
        "count": max(1, min(count, 100)),
    }
    if continuation:
        params["continuation"] = continuation

    headers = {"Authorization": f"Bearer {access_token.strip()}"}
    owns_client = client is None
    http = client or httpx.Client(timeout=45.0, follow_redirects=True)
    try:
        # Prefer query form used by Feedly docs (streamId as query param)
        response = http.get(FEEDLY_STREAMS_URL, params=params, headers=headers)
        if response.status_code == 404:
            # Fallback path-style URL
            encoded = quote(stream_id, safe="")
            response = http.get(
                f"https://cloud.feedly.com/v3/streams/{encoded}/contents",
                params={"count": params["count"]},
                headers=headers,
            )
        if response.status_code == 401:
            raise FeedlyError("Feedly auth failed (401) — check FEEDLY_ACCESS_TOKEN")
        if response.status_code == 403:
            raise FeedlyError("Feedly forbidden (403) — token may lack access to this stream")
        if response.status_code >= 400:
            raise FeedlyError(f"Feedly HTTP {response.status_code}: {response.text[:300]}")
        payload = response.json()
    except httpx.HTTPError as exc:
        raise FeedlyError(f"Feedly request failed: {exc}") from exc
    finally:
        if owns_client:
            http.close()

    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        items = []
    next_cont = payload.get("continuation") if isinstance(payload, dict) else None
    title = payload.get("title") if isinstance(payload, dict) else None
    cont_out = next_cont if isinstance(next_cont, str) else None
    title_out = title if isinstance(title, str) else None
    return items, cont_out, title_out


def pull_stream_articles(
    *,
    access_token: str,
    stream_id: str,
    count: int = 50,
    max_pages: int = 3,
    include_raw: bool = False,
    client: httpx.Client | None = None,
) -> list[RawArticle]:
    """Pull up to count*max_pages articles from a Feedly stream into RawArticle list."""
    articles: list[RawArticle] = []
    continuation: str | None = None
    stream_label: str | None = None

    for page in range(max_pages):
        items, continuation, title = fetch_stream_entries(
            access_token=access_token,
            stream_id=stream_id,
            count=count,
            continuation=continuation,
            client=client,
        )
        if title:
            stream_label = title
        for entry in items:
            if not isinstance(entry, dict):
                continue
            article = entry_to_raw_article(
                entry,
                stream_id=stream_id,
                stream_label=stream_label,
                include_raw=include_raw,
            )
            if article:
                articles.append(article)
        logger.info(
            "Feedly stream %s page %d: %d item(s)",
            stream_id,
            page + 1,
            len(items),
        )
        if not continuation or not items:
            break

    return articles
