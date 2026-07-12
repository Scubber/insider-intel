"""Text normalization helpers for ingestion/processing."""

from __future__ import annotations

import re
from html.parser import HTMLParser


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self._chunks.append(data)

    def get_text(self) -> str:
        return " ".join(self._chunks)


_WHITESPACE_RE = re.compile(r"\s+")


def strip_html(value: str) -> str:
    """Remove HTML tags and return plain text."""
    if not value:
        return ""
    if "<" not in value:
        return value
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(value)
        extractor.close()
        return extractor.get_text()
    except Exception:  # noqa: BLE001 — fall back to regex strip
        return re.sub(r"<[^>]+>", " ", value)


def normalize_whitespace(value: str) -> str:
    """Collapse runs of whitespace and trim."""
    return _WHITESPACE_RE.sub(" ", value).strip()


def to_plain_text(value: str | None) -> str:
    """HTML-strip + whitespace-normalize optional text."""
    if not value:
        return ""
    return normalize_whitespace(strip_html(value))
