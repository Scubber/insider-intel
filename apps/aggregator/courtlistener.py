"""CourtListener search → RawArticle (no PACER login / PDF purchase).

Uses the public Search API (type=r = federal RECAP dockets,
type=o = case law opinions).
Docs: https://www.courtlistener.com/help/api/rest/search/
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
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

OPINIONS_SOURCE_ID = "courtlistener-opinions"
OPINIONS_SOURCE_NAME = "CourtListener Opinions"

# Opinion snippets can run long; cap what we pack into RawArticle.summary.
SNIPPET_MAX_CHARS = 500


class CourtListenerError(RuntimeError):
    """CourtListener API request or parse failure."""


@dataclass(frozen=True)
class SearchTypeSpec:
    """One CourtListener Search API type and how to map its hits."""

    api_type: str
    source_id: str
    source_name: str
    order_by: str
    mapper: Callable[..., RawArticle | None]


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


def _format_citations(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        shown = [str(c).strip() for c in value[:5] if c]
        if shown:
            return "; ".join(shown)
    return None


def _opinion_snippet(hit: dict[str, Any]) -> str | None:
    # v4 opinion hits nest per-opinion snippets under "opinions".
    candidates: list[Any] = []
    opinions = hit.get("opinions")
    if isinstance(opinions, list):
        candidates.extend(
            op.get("snippet") for op in opinions if isinstance(op, dict)
        )
    candidates.append(hit.get("snippet"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:SNIPPET_MAX_CHARS]
    return None


def opinion_hit_to_raw_article(
    hit: dict[str, Any],
    *,
    query: str,
    include_raw: bool = False,
) -> RawArticle | None:
    """Map a case law opinion search hit to RawArticle."""
    link = _absolute_url(hit.get("absolute_url"))
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
    citations = _format_citations(hit.get("citation"))
    if citations:
        parts.append(f"Citations: {citations}")
    snippet = _opinion_snippet(hit)
    if snippet:
        parts.append(snippet)
    parts.append(f"CourtListener query: {query}")

    return RawArticle(
        title=title,
        link=link,
        published=_parse_date(hit.get("dateFiled") or hit.get("date_filed")),
        summary="\n".join(parts) if parts else None,
        source_id=OPINIONS_SOURCE_ID,
        source_name=OPINIONS_SOURCE_NAME,
        channel="filings",
        raw=hit if include_raw else None,
    )


SEARCH_TYPES: dict[str, SearchTypeSpec] = {
    "dockets": SearchTypeSpec(
        api_type="r",
        source_id=SOURCE_ID,
        source_name=SOURCE_NAME,
        order_by="dateFiled desc",
        mapper=hit_to_raw_article,
    ),
    "opinions": SearchTypeSpec(
        api_type="o",
        source_id=OPINIONS_SOURCE_ID,
        source_name=OPINIONS_SOURCE_NAME,
        order_by="dateFiled desc",
        mapper=opinion_hit_to_raw_article,
    ),
}

_TYPE_ALIASES: dict[str, str] = {
    "r": "dockets",
    "recap": "dockets",
    "docket": "dockets",
    "dockets": "dockets",
    "o": "opinions",
    "opinion": "opinions",
    "opinions": "opinions",
}


def _search(
    *,
    search_type: str,
    query: str,
    token: str | None = None,
    page_size: int = 20,
    max_pages: int = 1,
    order_by: str | None = None,
    include_raw: bool = False,
    client: httpx.Client | None = None,
) -> list[RawArticle]:
    """Run one Search API query for a SEARCH_TYPES entry."""
    spec = SEARCH_TYPES.get(search_type)
    if spec is None:
        raise CourtListenerError(f"unknown search type: {search_type!r}")

    headers: dict[str, str] = {"Accept": "application/json"}
    if token and token.strip():
        headers["Authorization"] = f"Token {token.strip()}"

    own_client = client is None
    http = client or httpx.Client(timeout=45.0, follow_redirects=True)
    articles: list[RawArticle] = []
    seen_links: set[str] = set()
    url: str | None = COURTLISTENER_SEARCH_URL
    params: dict[str, str | int] | None = {
        "type": spec.api_type,
        "q": query,
        "page_size": max(1, min(page_size, 100)),
        "order_by": order_by or spec.order_by,
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
                article = spec.mapper(hit, query=query, include_raw=include_raw)
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
        "CourtListener %s query %r → %d article(s)",
        search_type,
        query[:80],
        len(articles),
    )
    return articles


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
    return _search(
        search_type="dockets",
        query=query,
        token=token,
        page_size=page_size,
        max_pages=max_pages,
        order_by=order_by,
        include_raw=include_raw,
        client=client,
    )


def search_opinions(
    *,
    query: str,
    token: str | None = None,
    page_size: int = 20,
    max_pages: int = 1,
    order_by: str = "dateFiled desc",
    include_raw: bool = False,
    client: httpx.Client | None = None,
) -> list[RawArticle]:
    """Run one case law opinion (type=o) search and map results to RawArticle."""
    return _search(
        search_type="opinions",
        query=query,
        token=token,
        page_size=page_size,
        max_pages=max_pages,
        order_by=order_by,
        include_raw=include_raw,
        client=client,
    )


def parse_queries(raw: str | None, *, defaults: list[str] | None = None) -> list[str]:
    """Split comma-separated env queries; fall back to defaults."""
    fallback = defaults if defaults is not None else list(DEFAULT_QUERIES)
    if not raw or not raw.strip():
        return fallback
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or fallback


def parse_types(raw: str | None) -> list[str]:
    """Split comma-separated search types; empty falls back to dockets.

    Accepts aliases (r/recap → dockets, o → opinions) and "all".
    Raises ValueError on unknown values.
    """
    if not raw or not raw.strip():
        return ["dockets"]
    resolved: list[str] = []
    for part in raw.split(","):
        token = part.strip().lower()
        if not token:
            continue
        if token == "all":
            expanded = list(SEARCH_TYPES)
        elif token in _TYPE_ALIASES:
            expanded = [_TYPE_ALIASES[token]]
        else:
            valid = ", ".join(sorted({*_TYPE_ALIASES.values(), "all"}))
            raise ValueError(
                f"unknown CourtListener search type {part.strip()!r} "
                f"(expected one of: {valid})"
            )
        for name in expanded:
            if name not in resolved:
                resolved.append(name)
    return resolved or ["dockets"]
