"""CourtListener search → RawArticle, plus free-archive and Fetch clients.

Uses the public Search API (type=r = federal RECAP dockets,
type=o = case law opinions). All reads are free. The one paid surface is
``request_pacer_fetch`` (RECAP Fetch API) — every purchase decision lives in
apps/aggregator/pacer_purchase.py behind credentials + budget caps.
Docs: https://www.courtlistener.com/help/api/rest/search/
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urljoin

import httpx

from shared.schemas import RawArticle
from shared.utils.text import normalize_whitespace, strip_html

logger = logging.getLogger(__name__)

COURTLISTENER_SEARCH_URL = "https://www.courtlistener.com/api/rest/v4/search/"
COURTLISTENER_OPINION_DETAIL_URL = "https://www.courtlistener.com/api/rest/v4/opinions/"
COURTLISTENER_RECAP_DOCS_URL = "https://www.courtlistener.com/api/rest/v4/recap-documents/"
COURTLISTENER_CLUSTER_DETAIL_URL = "https://www.courtlistener.com/api/rest/v4/clusters/"
COURTLISTENER_DOCKET_ENTRIES_URL = "https://www.courtlistener.com/api/rest/v4/docket-entries/"
COURTLISTENER_FETCH_URL = "https://www.courtlistener.com/api/rest/v4/recap-fetch/"

# RECAP Fetch request types (https://www.courtlistener.com/help/api/rest/v4/recap/)
FETCH_TYPE_DOCKET = 1  # buy/refresh a docket report (populates entries)
FETCH_TYPE_PDF = 2  # buy one document PDF by recap_document id
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
    # Scattered-Spider-class prosecutions — employee-targeting social engineering
    # and the device/identity forensics (e.g. Windows GDID) their affidavits detail
    '"scattered spider"',
    '"sim swap" (fraud OR conspiracy OR indictment)',
    '"help desk" ("social engineering" OR impersonation OR credentials)',
    # Device/telemetry attribution forensics (GDID-class affidavits)
    '("device identifier" OR "digital fingerprint" OR telemetry) '
    "(VPN OR anonymized OR geolocation)",
]

SOURCE_ID = "courtlistener-recap"
SOURCE_NAME = "CourtListener RECAP"

OPINIONS_SOURCE_ID = "courtlistener-opinions"
OPINIONS_SOURCE_NAME = "CourtListener Opinions"

# Opinion snippets can run long; cap what we pack into RawArticle.summary.
SNIPPET_MAX_CHARS = 500

# Full opinion bodies feed ITM scoring via RawArticle.content; cap the size.
DEFAULT_OPINION_TEXT_MAX_CHARS = 20_000

# Concatenated RECAP filing bodies (complaint/indictment first) per docket.
DEFAULT_RECAP_TEXT_MAX_CHARS = 40_000

# Opinion detail text fields, in preference order (plain_text first).
_OPINION_TEXT_FIELDS = "plain_text,html,html_lawbox,html_columbia,xml_harvard"


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
    enricher: Callable[..., RawArticle] | None = None


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
    return RawArticle(
        title=title,
        link=link,
        published=_parse_date(hit.get("dateFiled") or hit.get("date_filed")),
        summary="\n".join(parts) if parts else None,
        # Scored (clean_text) but never displayed — keeps the match signal
        # out of the user-visible summary.
        content=f"CourtListener query: {query}",
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
        candidates.extend(op.get("snippet") for op in opinions if isinstance(op, dict))
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
    return RawArticle(
        title=title,
        link=link,
        published=_parse_date(hit.get("dateFiled") or hit.get("date_filed")),
        summary="\n".join(parts) if parts else None,
        content=f"CourtListener query: {query}",
        source_id=OPINIONS_SOURCE_ID,
        source_name=OPINIONS_SOURCE_NAME,
        channel="filings",
        raw=hit if include_raw else None,
    )


def _first_opinion_id(hit: dict[str, Any]) -> int | None:
    # The top-level hit "id" is the opinion *cluster* id — only the nested
    # opinions[].id is valid against /opinions/{id}/.
    opinions = hit.get("opinions")
    if isinstance(opinions, list):
        for op in opinions:
            if isinstance(op, dict) and isinstance(op.get("id"), int):
                return op["id"]
    return None


def fetch_opinion_text(
    opinion_id: int,
    *,
    token: str | None = None,
    max_chars: int = DEFAULT_OPINION_TEXT_MAX_CHARS,
    client: httpx.Client | None = None,
) -> str | None:
    """Fetch one opinion's full text as plain text (None when empty)."""
    headers: dict[str, str] = {"Accept": "application/json"}
    if token and token.strip():
        headers["Authorization"] = f"Token {token.strip()}"

    own_client = client is None
    http = client or httpx.Client(timeout=45.0, follow_redirects=True)
    try:
        try:
            resp = http.get(
                f"{COURTLISTENER_OPINION_DETAIL_URL}{opinion_id}/",
                headers=headers,
                params={"fields": _OPINION_TEXT_FIELDS},
            )
        except httpx.HTTPError as exc:
            raise CourtListenerError(f"opinion {opinion_id} fetch failed: {exc}") from exc
        if resp.status_code >= 400:
            raise CourtListenerError(
                f"opinion {opinion_id} HTTP {resp.status_code}: {resp.text[:300]}"
            )
        payload = resp.json()
    finally:
        if own_client:
            http.close()

    plain = payload.get("plain_text")
    if isinstance(plain, str) and plain.strip():
        return plain.strip()[:max_chars]
    for field in ("html", "html_lawbox", "html_columbia", "xml_harvard"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            text = normalize_whitespace(strip_html(value))
            if text:
                return text[:max_chars]
    return None


def enrich_opinion_article(
    article: RawArticle,
    hit: dict[str, Any],
    *,
    token: str | None = None,
    max_chars: int = DEFAULT_OPINION_TEXT_MAX_CHARS,
    client: httpx.Client | None = None,
) -> RawArticle:
    """Attach the full opinion body to article.content (best-effort).

    Never drops the article: on missing id or fetch failure the snippet-only
    article is returned unchanged.
    """
    opinion_id = _first_opinion_id(hit)
    if opinion_id is None:
        return article
    try:
        text = fetch_opinion_text(
            opinion_id,
            token=token,
            max_chars=max_chars,
            client=client,
        )
    except CourtListenerError as exc:
        logger.warning("Opinion text fetch failed for %s: %s", article.link, exc)
        return article
    if not text:
        return article
    combined = f"{article.content}\n{text}" if article.content else text
    return article.model_copy(update={"content": combined})


_DOCKET_LINK_RE = re.compile(r"/docket/(\d+)/")
_OPINION_LINK_RE = re.compile(r"/opinion/(\d+)/")


def parse_docket_id(link: str | None) -> int | None:
    """Docket id from a stored article link (/docket/{id}/{slug}/)."""
    match = _DOCKET_LINK_RE.search(link or "")
    return int(match.group(1)) if match else None


def parse_opinion_id(link: str | None) -> int | None:
    """Opinion cluster id from a stored article link (/opinion/{id}/{slug}/)."""
    match = _OPINION_LINK_RE.search(link or "")
    return int(match.group(1)) if match else None


def fetch_cluster_opinion_text(
    cluster_id: int,
    *,
    token: str | None = None,
    max_chars: int = DEFAULT_OPINION_TEXT_MAX_CHARS,
    client: httpx.Client | None = None,
) -> str | None:
    """Full text for an opinion *cluster* (what /opinion/{id}/ links carry).

    Cluster detail → first sub-opinion id → fetch_opinion_text. Two free GETs.
    """
    headers: dict[str, str] = {"Accept": "application/json"}
    if token and token.strip():
        headers["Authorization"] = f"Token {token.strip()}"

    own_client = client is None
    http = client or httpx.Client(timeout=45.0, follow_redirects=True)
    try:
        try:
            resp = http.get(
                f"{COURTLISTENER_CLUSTER_DETAIL_URL}{cluster_id}/",
                headers=headers,
                params={"fields": "sub_opinions"},
            )
        except httpx.HTTPError as exc:
            raise CourtListenerError(f"cluster {cluster_id} fetch failed: {exc}") from exc
        if resp.status_code >= 400:
            raise CourtListenerError(
                f"cluster {cluster_id} HTTP {resp.status_code}: {resp.text[:300]}"
            )
        payload = resp.json()
        opinion_id: int | None = None
        for sub in payload.get("sub_opinions") or []:
            match = re.search(r"/opinions/(\d+)/", str(sub))
            if match:
                opinion_id = int(match.group(1))
                break
        if opinion_id is None:
            return None
        return fetch_opinion_text(opinion_id, token=token, max_chars=max_chars, client=http)
    finally:
        if own_client:
            http.close()


def fetch_recap_document_text(
    docket_id: int,
    *,
    token: str | None = None,
    max_chars: int = DEFAULT_RECAP_TEXT_MAX_CHARS,
    page_size: int = 10,
    client: httpx.Client | None = None,
) -> str:
    """Concatenated plain text of a docket's RECAP documents.

    Free endpoint only: reads documents already uploaded to the RECAP archive
    (``is_available=true``) — never triggers a PACER purchase. Documents come
    back in id order, so the complaint/indictment (entry 1) leads. Returns ""
    when the archive has no text for this docket yet — common, not an error.
    """
    headers: dict[str, str] = {"Accept": "application/json"}
    if token and token.strip():
        headers["Authorization"] = f"Token {token.strip()}"

    own_client = client is None
    http = client or httpx.Client(timeout=45.0, follow_redirects=True)
    try:
        try:
            resp = http.get(
                COURTLISTENER_RECAP_DOCS_URL,
                headers=headers,
                params={
                    "docket_entry__docket__id": docket_id,
                    "is_available": "true",
                    "order_by": "id",
                    "page_size": max(1, min(page_size, 20)),
                    "fields": "plain_text,description,document_number",
                },
            )
        except httpx.HTTPError as exc:
            raise CourtListenerError(f"docket {docket_id} recap fetch failed: {exc}") from exc
        if resp.status_code >= 400:
            raise CourtListenerError(
                f"docket {docket_id} recap HTTP {resp.status_code}: {resp.text[:300]}"
            )
        payload = resp.json()
    finally:
        if own_client:
            http.close()

    parts: list[str] = []
    total = 0
    for doc in payload.get("results") or []:
        if not isinstance(doc, dict):
            continue
        text = doc.get("plain_text")
        if not isinstance(text, str) or not text.strip():
            continue
        desc = str(doc.get("description") or "").strip()
        number = doc.get("document_number")
        header = f"--- Document {number or '?'}{(': ' + desc) if desc else ''} ---"
        body = f"{header}\n{text.strip()}"
        if total + len(body) > max_chars:
            body = body[: max(0, max_chars - total)]
        if body:
            parts.append(body)
            total += len(body) + 2
        if total >= max_chars:
            break
    return "\n\n".join(parts)


def fetch_docket_entries(
    docket_id: int,
    *,
    token: str | None = None,
    page_size: int = 5,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Slim leading docket entries (the complaint/indictment lives up front).

    Returns [{entry_number, recap_documents: [{id, is_available,
    document_number}]}] in entry order. Free metadata read.
    """
    headers: dict[str, str] = {"Accept": "application/json"}
    if token and token.strip():
        headers["Authorization"] = f"Token {token.strip()}"

    own_client = client is None
    http = client or httpx.Client(timeout=45.0, follow_redirects=True)
    try:
        try:
            resp = http.get(
                COURTLISTENER_DOCKET_ENTRIES_URL,
                headers=headers,
                params={
                    "docket": docket_id,
                    "order_by": "entry_number",
                    "page_size": max(1, min(page_size, 20)),
                    "fields": "entry_number,recap_documents",
                },
            )
        except httpx.HTTPError as exc:
            raise CourtListenerError(f"docket {docket_id} entries fetch failed: {exc}") from exc
        if resp.status_code >= 400:
            raise CourtListenerError(
                f"docket {docket_id} entries HTTP {resp.status_code}: {resp.text[:300]}"
            )
        payload = resp.json()
    finally:
        if own_client:
            http.close()

    entries: list[dict[str, Any]] = []
    for entry in payload.get("results") or []:
        if not isinstance(entry, dict):
            continue
        docs = []
        for doc in entry.get("recap_documents") or []:
            if isinstance(doc, dict) and doc.get("id") is not None:
                docs.append(
                    {
                        "id": doc["id"],
                        "is_available": bool(doc.get("is_available")),
                        "document_number": doc.get("document_number"),
                    }
                )
        entries.append({"entry_number": entry.get("entry_number"), "recap_documents": docs})
    return entries


def request_pacer_fetch(
    *,
    request_type: int,
    token: str,
    pacer_username: str,
    pacer_password: str,
    recap_document: int | None = None,
    docket: int | None = None,
    client: httpx.Client | None = None,
) -> int | None:
    """Queue a PACER purchase via the RECAP Fetch API. Returns the request id.

    This SPENDS MONEY on the caller's PACER account ($0.10/page, $3/document
    cap; fees waived under $30/quarter). The purchased content joins the
    public RECAP archive, where the text backfill harvests it. Callers own
    all budget/qualification gating.
    """
    payload: dict[str, Any] = {
        "request_type": request_type,
        "pacer_username": pacer_username,
        "pacer_password": pacer_password,
    }
    if recap_document is not None:
        payload["recap_document"] = recap_document
    if docket is not None:
        payload["docket"] = docket

    headers = {
        "Accept": "application/json",
        "Authorization": f"Token {token.strip()}",
    }
    own_client = client is None
    http = client or httpx.Client(timeout=45.0, follow_redirects=True)
    try:
        try:
            resp = http.post(COURTLISTENER_FETCH_URL, headers=headers, data=payload)
        except httpx.HTTPError as exc:
            raise CourtListenerError(f"recap-fetch request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise CourtListenerError(f"recap-fetch HTTP {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
    finally:
        if own_client:
            http.close()
    fetch_id = body.get("id")
    return int(fetch_id) if isinstance(fetch_id, int) else None


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
        enricher=enrich_opinion_article,
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
    filed_after: str | None = None,
    include_raw: bool = False,
    fetch_content: bool = False,
    content_max_chars: int = DEFAULT_OPINION_TEXT_MAX_CHARS,
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
    if filed_after:
        params["filed_after"] = filed_after

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
                    "unauthorized — set COURTLISTENER_API_TOKEN " "(Free Law Project token)"
                )
            if resp.status_code >= 400:
                raise CourtListenerError(f"HTTP {resp.status_code}: {resp.text[:300]}")

            payload = resp.json()
            for hit in payload.get("results") or []:
                if not isinstance(hit, dict):
                    continue
                article = spec.mapper(hit, query=query, include_raw=include_raw)
                if article is None or article.link in seen_links:
                    continue
                seen_links.add(article.link)
                if fetch_content and spec.enricher is not None:
                    article = spec.enricher(
                        article,
                        hit,
                        token=token,
                        max_chars=content_max_chars,
                        client=http,
                    )
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
    filed_after: str | None = None,
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
        filed_after=filed_after,
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
    filed_after: str | None = None,
    include_raw: bool = False,
    fetch_content: bool = False,
    content_max_chars: int = DEFAULT_OPINION_TEXT_MAX_CHARS,
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
        filed_after=filed_after,
        include_raw=include_raw,
        fetch_content=fetch_content,
        content_max_chars=content_max_chars,
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
                f"unknown CourtListener search type {part.strip()!r} " f"(expected one of: {valid})"
            )
        for name in expanded:
            if name not in resolved:
                resolved.append(name)
    return resolved or ["dockets"]
