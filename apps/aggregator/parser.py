"""Parse RSS/Atom feed bodies into RawArticle models."""

from __future__ import annotations

import logging
from calendar import timegm
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser

from shared.schemas import FeedSource, RawArticle

logger = logging.getLogger(__name__)


class FeedParseError(Exception):
    """Raised when a feed body cannot be parsed into articles."""

    def __init__(self, source_id: str, message: str) -> None:
        self.source_id = source_id
        self.message = message
        super().__init__(f"{source_id}: {message}")


def _parse_published(entry: dict[str, Any]) -> datetime | None:
    """Best-effort published timestamp from a feed entry."""
    # Prefer structured time from feedparser
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime.fromtimestamp(timegm(parsed), tz=UTC)
            except (TypeError, ValueError, OverflowError):
                pass

    # Fall back to string fields
    for key in ("published", "updated"):
        value = entry.get(key)
        if not value:
            continue
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except (TypeError, ValueError, IndexError):
            try:
                # ISO-8601 style
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=UTC)
                return dt.astimezone(UTC)
            except ValueError:
                continue

    return None


def _entry_summary(entry: dict[str, Any]) -> str | None:
    summary = entry.get("summary") or entry.get("description")
    if summary:
        return str(summary).strip() or None

    content = entry.get("content")
    if isinstance(content, list) and content:
        value = content[0].get("value")
        if value:
            return str(value).strip() or None
    return None


def parse_feed(body: str, source: FeedSource, *, include_raw: bool = False) -> list[RawArticle]:
    """Parse feed XML/body into a list of RawArticle.

    Skips entries missing a title or link. Does not raise on individual
    bad entries; only raises if the document itself is unusable.
    """
    parsed = feedparser.parse(body)

    # bozo means feedparser recovered from a parse problem; still try entries
    if getattr(parsed, "bozo", False) and not parsed.entries:
        bozo_exc = getattr(parsed, "bozo_exception", None)
        raise FeedParseError(
            source.id,
            f"unparseable feed ({bozo_exc or 'unknown error'})",
        )

    articles: list[RawArticle] = []
    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            logger.debug(
                "Skipping entry from %s missing title or link",
                source.id,
            )
            continue

        raw_payload: dict[str, Any] | None = None
        if include_raw:
            # Keep a minimal serializable subset of the entry
            raw_payload = {
                k: entry.get(k)
                for k in ("id", "title", "link", "published", "updated", "summary", "tags")
                if k in entry
            }

        articles.append(
            RawArticle(
                title=title,
                link=link,
                published=_parse_published(entry),
                summary=_entry_summary(entry),
                source_id=source.id,
                source_name=source.name,
                channel=source.channel,
                raw=raw_payload,
            )
        )

    logger.info("Parsed %d article(s) from %s", len(articles), source.id)
    return articles
