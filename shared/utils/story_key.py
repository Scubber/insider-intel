"""Stable story fingerprints for multi-source article clustering.

Storage stays one row per URL; story_key only groups presentation / export.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

# Trailing " - Outlet" / " | Site" / " — Source" noise common in RSS titles.
_TRAILING_SOURCE_RE = re.compile(
    r"\s*[\|\u2013\u2014\-]\s*[^|]+$",
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]+")
_WS_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation / trailing source suffixes, collapse space."""
    text = (title or "").strip().lower()
    # Drop up to two trailing source suffixes (e.g. "foo - Krebs - RSS")
    for _ in range(2):
        stripped = _TRAILING_SOURCE_RE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    text = _NON_ALNUM_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def story_day(published: datetime | None, *, fallback: datetime | None = None) -> str:
    """UTC calendar day YYYY-MM-DD for the fingerprint."""
    dt = published or fallback or datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return dt.date().isoformat()


def compute_story_key(
    title: str,
    published: datetime | None = None,
    *,
    fallback: datetime | None = None,
) -> str:
    """Hash of normalized title + publish day (stable across sources)."""
    norm = normalize_title(title)
    day = story_day(published, fallback=fallback)
    payload = f"{norm}|{day}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:16]


def cluster_bucket_key(story_key: str, channel: str) -> str:
    """Stream clusters only within a channel."""
    ch = (channel or "news").strip().lower() or "news"
    return f"{ch}:{story_key}"
