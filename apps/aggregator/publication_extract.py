"""Fetch a publication (landing page + PDF) and extract its full text.

Landing-page metadata reuses html_extract; the net-new pieces are PDF link
discovery, PDF text extraction (pypdf), and a body-text HTML fallback.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from apps.aggregator.html_extract import extract_article_html

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")
_SKIP_TEXT_TAGS = {"script", "style", "noscript", "template", "head"}


class PublicationFetchError(Exception):
    def __init__(self, url: str, message: str) -> None:
        self.url = url
        self.message = message
        super().__init__(f"{url}: {message}")


@dataclass
class PublicationDoc:
    """Extracted publication: metadata plus the fullest text body we found."""

    title: str
    summary: str | None
    published: datetime | None
    content: str | None
    pdf_url: str | None  # the PDF the content came from, if any


class _PdfLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = next((v for k, v in attrs if k.lower() == "href" and v), None)
        if href and href.split("?")[0].split("#")[0].lower().endswith(".pdf"):
            self.hrefs.append(href)


class _BodyTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in _SKIP_TEXT_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _SKIP_TEXT_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data)


def find_pdf_links(body: str, base_url: str) -> list[str]:
    """Return absolute PDF URLs linked from an HTML document, in page order."""
    parser = _PdfLinkParser()
    try:
        parser.feed(body)
        parser.close()
    except Exception as exc:  # noqa: BLE001 — tolerate broken HTML
        logger.debug("PDF link parse recovered: %s", exc)
    seen: set[str] = set()
    links: list[str] = []
    for href in parser.hrefs:
        absolute = urljoin(base_url, href)
        if absolute not in seen:
            seen.add(absolute)
            links.append(absolute)
    return links


def extract_page_text(body: str) -> str:
    """Visible-text fallback when no PDF is available."""
    parser = _BodyTextParser()
    try:
        parser.feed(body)
        parser.close()
    except Exception as exc:  # noqa: BLE001 — tolerate broken HTML
        logger.debug("Body text parse recovered: %s", exc)
    return _WS_RE.sub(" ", " ".join(parser.parts)).strip()


def extract_pdf_text(data: bytes, *, max_chars: int) -> str:
    """Extract plain text from PDF bytes, stopping once max_chars is reached."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    total = 0
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        parts.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return _WS_RE.sub(" ", "\n".join(parts)).strip()[:max_chars]


def _get(client: httpx.Client, url: str, *, user_agent: str, accept: str) -> httpx.Response:
    try:
        resp = client.get(url, headers={"User-Agent": user_agent, "Accept": accept})
        resp.raise_for_status()
        return resp
    except httpx.HTTPStatusError as exc:
        raise PublicationFetchError(url, f"HTTP {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        raise PublicationFetchError(url, f"request failed: {exc}") from exc


def _fetch_pdf_text(
    client: httpx.Client,
    url: str,
    *,
    user_agent: str,
    max_chars: int,
    max_bytes: int,
) -> str:
    resp = _get(client, url, user_agent=user_agent, accept="application/pdf,*/*;q=0.8")
    if len(resp.content) > max_bytes:
        raise PublicationFetchError(url, f"PDF exceeds {max_bytes} bytes")
    return extract_pdf_text(resp.content, max_chars=max_chars)


def _looks_like_pdf(resp: httpx.Response) -> bool:
    ctype = (resp.headers.get("content-type") or "").lower()
    return "application/pdf" in ctype or resp.content[:5] == b"%PDF-"


def fetch_publication(
    url: str,
    *,
    client: httpx.Client | None = None,
    pdf_url: str | None = None,
    user_agent: str,
    content_max_chars: int,
    pdf_max_bytes: int,
) -> PublicationDoc:
    """Fetch one publication URL: PDF text preferred, page text as fallback.

    Raises PublicationFetchError when the landing URL itself is unreachable;
    PDF failures degrade to landing-page text instead of failing the source.
    """
    owns_client = client is None
    http = client or httpx.Client(timeout=45.0, follow_redirects=True)
    try:
        resp = _get(
            http,
            url,
            user_agent=user_agent,
            accept="text/html,application/xhtml+xml;q=0.9,application/pdf;q=0.9,*/*;q=0.8",
        )
        if _looks_like_pdf(resp):
            if len(resp.content) > pdf_max_bytes:
                raise PublicationFetchError(url, f"PDF exceeds {pdf_max_bytes} bytes")
            text = extract_pdf_text(resp.content, max_chars=content_max_chars)
            return PublicationDoc(
                title="", summary=None, published=None, content=text or None, pdf_url=url
            )

        body = resp.text
        meta = extract_article_html(body)
        candidates = [pdf_url] if pdf_url else find_pdf_links(body, str(resp.url))
        content: str | None = None
        used_pdf: str | None = None
        for candidate in candidates[:3]:
            try:
                text = _fetch_pdf_text(
                    http,
                    candidate,
                    user_agent=user_agent,
                    max_chars=content_max_chars,
                    max_bytes=pdf_max_bytes,
                )
            except Exception as exc:  # noqa: BLE001 — degrade to page text
                logger.warning("PDF extraction failed for %s: %s", candidate, exc)
                continue
            if text:
                content = text
                used_pdf = candidate
                break
        if content is None:
            content = extract_page_text(body)[:content_max_chars] or None
        return PublicationDoc(
            title=meta.title,
            summary=meta.summary,
            published=meta.published,
            content=content,
            pdf_url=used_pdf,
        )
    finally:
        if owns_client:
            http.close()
