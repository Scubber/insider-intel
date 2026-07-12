"""Sitemap-backed archive sources for historical article backfill.

Same source_id as RSS feeds so the UI Sources list stays unified.
See docs/sourcing.md — New Source checklist step 2.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.schemas import Channel


@dataclass(frozen=True)
class ArchiveSource:
    """A publisher with a public sitemap (or sitemap index) for backfill."""

    id: str
    name: str
    sitemap_url: str
    category: str | None = None
    channel: Channel = "news"
    # Optional URL path substrings that must match (empty = any path from sitemap)
    url_path_hints: tuple[str, ...] = ()
    # Prefer child sitemap docs whose URL contains one of these (e.g. monthly archives)
    sitemap_child_hints: tuple[str, ...] = ()
    # Skip child sitemap docs containing these substrings
    skip_sitemap_substrings: tuple[str, ...] = ()


# IF038-class / multi-domain employment-risk phrases (URL + title/summary match).
DEFAULT_ARCHIVE_KEYWORDS: tuple[str, ...] = (
    "overemployment",
    "overemployed",
    "moonlighting",
    "concurrent employment",
    "dual employment",
    "outside employment",
    "conflict of interest",
    "side hustle",
    "remote work policy",
    "undisclosed employment",
    "second job",
)


DEFAULT_ARCHIVE_SOURCES: list[ArchiveSource] = [
    ArchiveSource(
        id="hrdive",
        name="HR Dive",
        sitemap_url="https://www.hrdive.com/sitemap.xml",
        category="hr",
        channel="news",
        url_path_hints=("/news/",),
        sitemap_child_hints=("/news/archive/",),
        skip_sitemap_substrings=("/sitemap-footer", "/sitemap-topics"),
    ),
    ArchiveSource(
        id="proskauer-workplace",
        name="Proskauer Law and the Workplace",
        sitemap_url="https://www.lawandtheworkplace.com/sitemap_index.xml",
        category="legal",
        channel="news",
    ),
]


def get_archive_sources(
    source_ids: list[str] | None = None,
) -> list[ArchiveSource]:
    """Return configured archive sources, optionally filtered by id."""
    if not source_ids:
        return list(DEFAULT_ARCHIVE_SOURCES)
    wanted = {s.strip().lower() for s in source_ids if s and s.strip()}
    return [s for s in DEFAULT_ARCHIVE_SOURCES if s.id.lower() in wanted]
