"""Fetch and walk XML sitemaps / sitemap indexes."""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from urllib.parse import urlparse

import httpx

from apps.aggregator.fetcher import DEFAULT_USER_AGENT

logger = logging.getLogger(__name__)


class SitemapError(Exception):
    """Raised when a sitemap cannot be fetched or parsed."""

    def __init__(self, url: str, message: str) -> None:
        self.url = url
        self.message = message
        super().__init__(f"{url}: {message}")


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _iter_locs(root: ET.Element) -> Iterable[tuple[str, str]]:
    """Yield (kind, loc) where kind is 'url' or 'sitemap'."""
    for el in root.iter():
        name = _local(el.tag)
        if name not in {"url", "sitemap"}:
            continue
        loc_el = None
        for child in el:
            if _local(child.tag) == "loc" and child.text:
                loc_el = child
                break
        if loc_el is None or not loc_el.text:
            continue
        yield name, loc_el.text.strip()


def parse_sitemap_xml(body: str) -> tuple[list[str], list[str]]:
    """Parse sitemap body into (page_urls, child_sitemap_urls)."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise SitemapError("<memory>", f"XML parse error: {exc}") from exc

    pages: list[str] = []
    children: list[str] = []
    for kind, loc in _iter_locs(root):
        if not loc.startswith("http"):
            continue
        if kind == "sitemap":
            children.append(loc)
        else:
            pages.append(loc)
    return pages, children


def fetch_sitemap_body(
    url: str,
    *,
    client: httpx.Client,
    user_agent: str = DEFAULT_USER_AGENT,
) -> str:
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/xml, text/xml, */*",
    }
    try:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise SitemapError(url, f"HTTP {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        raise SitemapError(url, f"request failed: {exc}") from exc
    body = resp.text
    if not body or not body.strip():
        raise SitemapError(url, "empty response body")
    return body


def collect_sitemap_urls(
    sitemap_url: str,
    *,
    client: httpx.Client,
    max_sitemaps: int = 40,
    max_urls: int = 50_000,
    delay_seconds: float = 0.25,
    path_hints: tuple[str, ...] = (),
    child_hints: tuple[str, ...] = (),
    skip_sitemap_substrings: tuple[str, ...] = (),
) -> list[str]:
    """Walk a sitemap or sitemap index and return page URLs.

    When path_hints is non-empty, only URLs whose path contains one of the
    hints (case-insensitive) are kept. Child sitemap docs matching
    child_hints are queued first; skip_sitemap_substrings are ignored.
    """
    seen_sitemaps: set[str] = set()
    queue: list[str] = [sitemap_url]
    pages: list[str] = []
    hints = tuple(h.lower() for h in path_hints if h)
    child_pref = tuple(h.lower() for h in child_hints if h)
    skip_subs = tuple(s.lower() for s in skip_sitemap_substrings if s)

    def _should_skip_child(url: str) -> bool:
        ul = url.lower()
        return any(s in ul for s in skip_subs)

    def _enqueue_children(child_urls: list[str]) -> None:
        preferred: list[str] = []
        rest: list[str] = []
        for child in child_urls:
            if child in seen_sitemaps or child in queue:
                continue
            if _should_skip_child(child):
                continue
            if child_pref and any(h in child.lower() for h in child_pref):
                preferred.append(child)
            else:
                rest.append(child)
        # Newest monthly archives tend to be listed first; keep that order.
        queue[0:0] = preferred + rest

    while queue and len(seen_sitemaps) < max_sitemaps and len(pages) < max_urls:
        url = queue.pop(0)
        if url in seen_sitemaps:
            continue
        seen_sitemaps.add(url)
        logger.info("Fetching sitemap (%d/%d): %s", len(seen_sitemaps), max_sitemaps, url)
        try:
            body = fetch_sitemap_body(url, client=client)
            page_urls, child_urls = parse_sitemap_xml(body)
        except SitemapError as exc:
            logger.warning("Skipping sitemap %s: %s", url, exc)
            continue

        _enqueue_children(child_urls)

        for page in page_urls:
            if len(pages) >= max_urls:
                break
            if hints:
                path = urlparse(page).path.lower()
                if not any(h in path for h in hints):
                    continue
            pages.append(page)

        if delay_seconds > 0 and (queue or page_urls):
            time.sleep(delay_seconds)

    logger.info(
        "Sitemap walk complete: %d page URL(s) from %d sitemap doc(s)",
        len(pages),
        len(seen_sitemaps),
    )
    return pages


def url_matches_keywords(url: str, keywords: Iterable[str]) -> bool:
    """True if any keyword appears in the URL (case-insensitive)."""
    hay = url.lower()
    return any(kw.lower() in hay for kw in keywords if kw.strip())
