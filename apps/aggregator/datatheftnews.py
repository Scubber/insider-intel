"""DataTheftNews client — public Supabase REST for published blog posts.

The site (https://datatheftnews.com) is a React SPA with no public RSS/sitemap.
Articles live in Supabase ``blog_posts`` (anon key is embedded in their frontend).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from shared.utils.text import to_plain_text

logger = logging.getLogger(__name__)

SOURCE_ID = "datatheftnews"
SOURCE_NAME = "DataTheftNews"
SITE_ORIGIN = "https://www.datatheftnews.com"
DEFAULT_SUPABASE_URL = "https://efjoefkaplfsgqwrbseg.supabase.co"
# Public anon JWT shipped in their frontend (RLS; read published posts only).
DEFAULT_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVmam9lZmthcGxmc2dxd3Jic2VnIiwicm9sZSI6ImFub24i"
    "LCJpYXQiOjE3NDg5NTM1NTEsImV4cCI6MjA2NDUyOTU1MX0."
    "GcjbnnRL-nN5kMEKOS1KxoUa3QfuTrixw9RqW8Tzy7k"
)


def post_url(slug: str) -> str:
    return f"{SITE_ORIGIN}/blog/{slug.strip().lstrip('/')}"


def parse_published(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def row_to_article(
    row: dict[str, Any],
    *,
    include_raw: bool = False,
    content_max_chars: int = 50_000,
) -> dict[str, Any] | None:
    """Map a blog_posts row to RawArticle field kwargs (or None if unusable)."""
    title = (row.get("title") or "").strip()
    slug = (row.get("slug") or "").strip()
    if not title or not slug:
        return None
    excerpt = (row.get("excerpt") or "").strip() or None
    plain = to_plain_text(row.get("content"))
    if content_max_chars > 0 and len(plain) > content_max_chars:
        plain = plain[:content_max_chars]
    summary = excerpt
    if not summary and plain:
        summary = plain[:500] + ("…" if len(plain) > 500 else "")
    category = row.get("category")
    tags = row.get("tags") or []
    meta_bits: list[str] = []
    if category:
        meta_bits.append(f"category={category}")
    if isinstance(tags, list) and tags:
        meta_bits.append("tags=" + ",".join(str(t) for t in tags[:12]))
    if meta_bits and summary:
        summary = f"{summary}\n\n({' · '.join(meta_bits)})"
    elif meta_bits:
        summary = " · ".join(meta_bits)

    payload: dict[str, Any] = {
        "title": title,
        "link": post_url(slug),
        "published": parse_published(row.get("published_at") or row.get("created_at")),
        "summary": summary,
        "content": plain or None,
        "source_id": SOURCE_ID,
        "source_name": SOURCE_NAME,
        "channel": "news",
    }
    if include_raw:
        payload["raw"] = {
            "id": row.get("id"),
            "slug": slug,
            "category": category,
            "tags": tags,
            "published_at": row.get("published_at"),
        }
    return payload


def resolve_anon_key(configured: str | None = None) -> str:
    """Use configured key, discover from SPA bundles, or fall back to baked public key."""
    if configured and configured.strip():
        return configured.strip()
    try:
        index = httpx.get(f"{SITE_ORIGIN}/", timeout=20, follow_redirects=True)
        index.raise_for_status()
        m_index = re.search(r"assets/(index-[A-Za-z0-9_-]+\.js)", index.text)
        blob = index.text
        if m_index:
            main = httpx.get(
                f"{SITE_ORIGIN}/assets/{m_index.group(1)}",
                timeout=20,
                follow_redirects=True,
            )
            main.raise_for_status()
            blob = main.text
            m_blog = re.search(r"(?:assets/)?(blogService-[A-Za-z0-9_-]+\.js)", blob)
            if m_blog:
                js = httpx.get(
                    f"{SITE_ORIGIN}/assets/{m_blog.group(1)}",
                    timeout=20,
                    follow_redirects=True,
                )
                js.raise_for_status()
                blob = js.text
        keys = re.findall(r"eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+", blob)
        if keys:
            return keys[0]
    except Exception:
        logger.warning("Falling back to baked DataTheftNews anon key", exc_info=True)
    return DEFAULT_ANON_KEY


def fetch_published_posts(
    *,
    supabase_url: str = DEFAULT_SUPABASE_URL,
    anon_key: str,
    limit: int = 200,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """GET published blog_posts via Supabase REST (paginated)."""
    base = supabase_url.rstrip("/") + "/rest/v1/blog_posts"
    headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
        "Accept": "application/json",
    }
    out: list[dict[str, Any]] = []
    page_size = min(max(limit, 1), 100)
    offset = 0
    with httpx.Client(timeout=timeout, headers=headers) as client:
        while len(out) < limit:
            end = offset + page_size - 1
            response = client.get(
                base,
                headers={**headers, "Range": f"{offset}-{end}"},
                params={
                    "select": (
                        "id,slug,title,excerpt,content,category,tags,"
                        "is_published,published_at,created_at"
                    ),
                    "is_published": "eq.true",
                    "order": "published_at.desc.nullslast",
                },
            )
            if response.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"DataTheftNews Supabase HTTP {response.status_code}: "
                    f"{response.text[:300]}",
                    request=response.request,
                    response=response,
                )
            batch = response.json()
            if not isinstance(batch, list) or not batch:
                break
            out.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
    return out[:limit]
