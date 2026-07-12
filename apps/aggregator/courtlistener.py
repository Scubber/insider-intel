"""CourtListener RECAP search → RawArticle (no PACER login / PDF purchase).

Uses the public Search API (type=r = federal RECAP dockets).
Docs: https://www.courtlistener.com/help/api/rest/search/
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urljoin

import httpx

from shared.schemas import RawArticle

logger = logging.getLogger(__name__)

COURTLISTENER_SEARCH_URL = "https://www.courtlistener.com/api/rest/v4/search/"
COURTLISTENER_BASE = "https://www.courtlistener.com"

DEFAULT_QUERIES: list[str] = [
    '"insider trading"',
    '"trade secret" (employee OR contractor OR "former employee")',
    '"economic espionage"',
    '"computer fraud" (employee OR contractor OR insider)',
    # Multi-domain IF038-class — prefer policy/disclosure/termination language (less noise)
    'moonlighting (policy OR disclosure OR terminate OR "code of conduct" OR employment)',
    '"concurrent employment" (employee OR disclosure OR terminate OR policy)',
    '"dual employment" (employee OR disclosure OR terminate OR policy)',
    '"outside employment" (policy OR disclosure OR terminate OR employee)',
    '"conflict of interest" ("outside employment" OR moonlighting OR "second job")',
]

SOURCE_ID = "courtlistener-recap"
SOURCE_NAME = "CourtListener RECAP"


class CourtListenerError(RuntimeError):
    """CourtListener API request or parse failure."""


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # API returns YYYY-MM-DD for dateFiled
        if len(value) == 10 and value[4] == "-":
            d = date.fromisoformat(value)
            return datetime(d.year, d.month, d.day, tzinfo=UTC)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _absolute_url(path_or_url: str | None) -> str | None:
    if not path_or_url:
        return None
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    return urljoin(COURTLISTENER_BASE, path_or_url)


def hit_to_raw_article(
    hit: dict[str, Any],
    *,
    query: str,
    include_raw: bool = False,
) -> RawArticle | None:
    """Map a RECAP search hit to RawArticle."""
    link = _absolute_url(hit.get("docket_absolute_url") or hit.get("absolute_url"))
    title = (hit.get("caseName") or hit.get("case_name_full") or "").strip()
    if not link or not title:
        return None

    parts: list[str] = []
    court = hit.get("court") or hit.get("court_citation_string")
    if court:
        parts.append(f"Court: {court}")
    docket = hit.get("docketNumber")
    if docket:
        parts.append(f"Docket: {docket}")
    cause = hit.get("cause")
    if cause:
        parts.append(f"Cause: {cause}")
    parties = hit.get("party")
    if isinstance(parties, list) and parties:
        shown = [str(p) for p in parties[:8] if p]
        if shown:
            parts.append("Parties: " + "; ".join(shown))
    parts.append(f"CourtListener query: {query}")

    return RawArticle(
        title=title,
        link=link,
        published=_parse_date(hit.get("dateFiled") or hit.get("date_filed")),
        summary="\n".join(parts) if parts else None,
        source_id=SOURCE_ID,
        source_name=SOURCE_NAME,
        channel="filings",
        raw=hit if include_raw else None,
    )


def search_recap(
    *,
    query: str,
    token: str | None = None,
    page_size: int = 20,
    max_pages: int = 1,
    order_by: str = "dateFiled desc",
    include_raw: bool = False,
    client: httpx.Client | None = None,
) -> list[RawArticle]:
    """Run one RECAP (type=r) search and map results to RawArticle."""
    headers: dict[str, str] = {"Accept": "application/json"}
    if token and token.strip():
        headers["Authorization"] = f"Token {token.strip()}"

    own_client = client is None
    http = client or httpx.Client(timeout=45.0, follow_redirects=True)
    articles: list[RawArticle] = []
    seen_links: set[str] = set()
    url: str | None = COURTLISTENER_SEARCH_URL
    params: dict[str, str | int] | None = {
        "type": "r",
        "q": query,
        "page_size": max(1, min(page_size, 100)),
        "order_by": order_by,
    }

    try:
        for _ in range(max(1, max_pages)):
            if not url:
                break
            try:
                if params is not None:
                    resp = http.get(url, headers=headers, params=params)
                else:
                    resp = http.get(url, headers=headers)
            except httpx.HTTPError as exc:
                raise CourtListenerError(f"request failed: {exc}") from exc

            if resp.status_code == 401:
                raise CourtListenerError(
                    "unauthorized — set COURTLISTENER_API_TOKEN "
                    "(Free Law Project token)"
                )
            if resp.status_code >= 400:
                raise CourtListenerError(
                    f"HTTP {resp.status_code}: {resp.text[:300]}"
                )

            payload = resp.json()
            for hit in payload.get("results") or []:
                if not isinstance(hit, dict):
                    continue
                article = hit_to_raw_article(hit, query=query, include_raw=include_raw)
                if article is None or article.link in seen_links:
                    continue
                seen_links.add(article.link)
                articles.append(article)

            next_url = payload.get("next")
            url = next_url if isinstance(next_url, str) else None
            params = None  # next URL already encodes cursor/params
    finally:
        if own_client:
            http.close()

    logger.info(
        "CourtListener query %r → %d article(s)",
        query[:80],
        len(articles),
    )
    return articles


def parse_queries(raw: str | None, *, defaults: list[str] | None = None) -> list[str]:
    """Split comma-separated env queries; fall back to defaults."""
    fallback = defaults if defaults is not None else list(DEFAULT_QUERIES)
    if not raw or not raw.strip():
        return fallback
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or fallback
