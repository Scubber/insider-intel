"""Lightweight HTML article metadata extraction (no extra deps)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

import httpx

from apps.aggregator.fetcher import DEFAULT_USER_AGENT

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class HtmlExtractError(Exception):
    def __init__(self, url: str, message: str) -> None:
        self.url = url
        self.message = message
        super().__init__(f"{url}: {message}")


@dataclass
class ExtractedArticle:
    title: str
    summary: str | None
    published: datetime | None
    raw_meta: dict[str, Any] | None = None


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self._in_title = False
        self.metas: list[dict[str, str]] = []
        self.time_datetimes: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k.lower(): (v or "") for k, v in attrs}
        t = tag.lower()
        if t == "title":
            self._in_title = True
        elif t == "meta":
            self.metas.append(ad)
        elif t == "time" and ad.get("datetime"):
            self.time_datetimes.append(ad["datetime"])

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)


def _strip(value: str | None) -> str:
    if not value:
        return ""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", value)).strip()


def _meta_content(metas: list[dict[str, str]], *keys: str) -> str | None:
    wanted = {k.lower() for k in keys}
    for m in metas:
        name = (m.get("name") or m.get("property") or m.get("itemprop") or "").lower()
        if name in wanted:
            content = (m.get("content") or "").strip()
            if content:
                return content
    return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def extract_article_html(body: str) -> ExtractedArticle:
    """Extract title / summary / published from an HTML document body."""
    parser = _MetaParser()
    try:
        parser.feed(body)
        parser.close()
    except Exception as exc:  # noqa: BLE001 — tolerate broken HTML
        logger.debug("HTML parse recovered: %s", exc)

    title = _strip("".join(parser.title_parts))
    og_title = _meta_content(parser.metas, "og:title", "twitter:title")
    if og_title:
        title = _strip(og_title) or title

    summary = _meta_content(
        parser.metas,
        "og:description",
        "description",
        "twitter:description",
    )
    summary = _strip(summary) or None

    published = None
    for key in (
        "article:published_time",
        "og:published_time",
        "pubdate",
        "publish-date",
        "date",
        "DC.date.issued",
    ):
        published = _parse_datetime(_meta_content(parser.metas, key))
        if published:
            break
    if published is None:
        for td in parser.time_datetimes:
            published = _parse_datetime(td)
            if published:
                break

    return ExtractedArticle(
        title=title,
        summary=summary,
        published=published,
        raw_meta={
            "meta_keys": sorted(
                {
                    (m.get("name") or m.get("property") or m.get("itemprop") or "")
                    for m in parser.metas
                    if (m.get("name") or m.get("property") or m.get("itemprop"))
                }
            )[:40],
        },
    )


def fetch_and_extract(
    url: str,
    *,
    client: httpx.Client,
    user_agent: str = DEFAULT_USER_AGENT,
    include_raw: bool = False,
) -> ExtractedArticle:
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }
    try:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HtmlExtractError(url, f"HTTP {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        raise HtmlExtractError(url, f"request failed: {exc}") from exc

    extracted = extract_article_html(resp.text)
    if not extracted.title:
        raise HtmlExtractError(url, "no title found")
    if not include_raw:
        extracted.raw_meta = None
    return extracted


def text_matches_keywords(text: str, keywords: list[str] | tuple[str, ...]) -> bool:
    hay = (text or "").lower()
    return any(kw.lower() in hay for kw in keywords if kw.strip())
