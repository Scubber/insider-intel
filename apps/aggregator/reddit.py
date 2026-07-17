"""Reddit client — OAuth app auth when configured, public JSON otherwise.

Reddit rejects default HTTP user agents (and 429s unauthenticated requests
from cloud provider IPs); always send a descriptive UA, and configure
REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET (a free "script" app) so listing
fetches go through oauth.reddit.com. Supports subreddit /new listings plus
single-post ingestion by URL (the manual "flag this post" path), including
/s/ share links via redirect — the single-post path stays on public JSON.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from apps.aggregator.social_subscriptions import normalize_handle, social_source_id
from shared.utils.text import to_plain_text

logger = logging.getLogger(__name__)

REDDIT_ORIGIN = "https://www.reddit.com"
OAUTH_ORIGIN = "https://oauth.reddit.com"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
DEFAULT_USER_AGENT = "insider-intel/0.1 (insider-threat research aggregator)"

# App token cache: (access_token, monotonic_expiry). client_credentials
# tokens last ~24h; refresh a minute early.
_token_cache: tuple[str, float] | None = None


def _get_app_token(
    client_id: str,
    client_secret: str,
    *,
    user_agent: str,
    timeout: float = 20.0,
    client: httpx.Client | None = None,
) -> str:
    global _token_cache
    if _token_cache and time.monotonic() < _token_cache[1]:
        return _token_cache[0]
    owns_client = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        response = client.post(
            TOKEN_URL,
            auth=(client_id, client_secret),
            headers={"User-Agent": user_agent},
            data={"grant_type": "client_credentials"},
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_client:
            client.close()
    token = payload["access_token"]
    expires_in = float(payload.get("expires_in") or 3600)
    _token_cache = (token, time.monotonic() + max(expires_in - 60.0, 60.0))
    return token


def _clear_app_token() -> None:
    global _token_cache
    _token_cache = None


def subreddit_source(sub: str) -> tuple[str, str]:
    """(source_id, source_name) for a subreddit."""
    normalized = normalize_handle("reddit", sub)
    return social_source_id("reddit", normalized), f"Reddit r/{normalized}"


def _get_json(
    url: str,
    *,
    user_agent: str,
    params: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> Any:
    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        response = client.get(url, params=params)
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            wait = min(float(retry_after), 30.0) if retry_after else 5.0
            logger.warning("Reddit 429 for %s; retrying after %.0fs", url, wait)
            time.sleep(wait)
            response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()


def _oauth_get_json(
    url: str,
    *,
    client_id: str,
    client_secret: str,
    user_agent: str,
    params: dict[str, Any] | None = None,
    timeout: float = 20.0,
    client: httpx.Client | None = None,
) -> Any:
    """GET against oauth.reddit.com with a cached app token; one 401 retry."""
    owns_client = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        for attempt in (1, 2):
            token = _get_app_token(
                client_id, client_secret, user_agent=user_agent, client=client
            )
            response = client.get(
                url,
                params=params,
                headers={
                    "User-Agent": user_agent,
                    "Accept": "application/json",
                    "Authorization": f"bearer {token}",
                },
            )
            if response.status_code == 401 and attempt == 1:
                _clear_app_token()  # expired/revoked token — mint a fresh one
                continue
            response.raise_for_status()
            return response.json()
    finally:
        if owns_client:
            client.close()


def fetch_subreddit_new(
    sub: str,
    *,
    limit: int = 50,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = 20.0,
    client_id: str | None = None,
    client_secret: str | None = None,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Newest posts for a subreddit as raw t3 data dicts.

    With client_id/client_secret set, uses OAuth app auth against
    oauth.reddit.com (survives cloud-IP 429 blocking); otherwise falls back
    to the public JSON endpoint.
    """
    normalized = normalize_handle("reddit", sub)
    params = {"limit": min(max(limit, 1), 100), "raw_json": 1}
    if client_id and client_secret:
        payload = _oauth_get_json(
            f"{OAUTH_ORIGIN}/r/{normalized}/new",
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
            params=params,
            timeout=timeout,
            client=client,
        )
    else:
        payload = _get_json(
            f"{REDDIT_ORIGIN}/r/{normalized}/new.json",
            user_agent=user_agent,
            params=params,
            timeout=timeout,
        )
    children = (payload or {}).get("data", {}).get("children", [])
    return [
        child.get("data", {})
        for child in children
        if child.get("kind") == "t3" and child.get("data")
    ]


def fetch_post_by_url(
    url: str,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = 20.0,
) -> dict[str, Any] | None:
    """Fetch a single post's t3 data from any Reddit post/share URL."""
    parts = urlsplit(url.strip())
    if "reddit.com" not in parts.netloc.lower():
        raise ValueError(f"not a reddit URL: {url}")
    # Share links (/r/<sub>/s/<token>) redirect to the canonical post URL;
    # follow_redirects in _get_json handles that when we request .json.
    path = parts.path.rstrip("/")
    if not path.endswith(".json"):
        path += ".json"
    payload = _get_json(
        urlunsplit(("https", "www.reddit.com", path, "", "")),
        user_agent=user_agent,
        params={"raw_json": 1},
        timeout=timeout,
    )
    # Post pages return [listing(post), listing(comments)].
    listing = payload[0] if isinstance(payload, list) and payload else payload
    children = (listing or {}).get("data", {}).get("children", [])
    for child in children:
        if child.get("kind") == "t3" and child.get("data"):
            return child["data"]
    return None


def post_to_article(
    post: dict[str, Any],
    *,
    include_raw: bool = False,
    content_max_chars: int = 20_000,
) -> dict[str, Any] | None:
    """Map a t3 post to RawArticle field kwargs (or None if unusable)."""
    title = (post.get("title") or "").strip()
    permalink = (post.get("permalink") or "").strip()
    sub = (post.get("subreddit") or "").strip()
    if not title or not permalink or not sub:
        return None
    if post.get("stickied") or post.get("promoted"):
        return None

    plain = to_plain_text(post.get("selftext") or "")
    if content_max_chars > 0 and len(plain) > content_max_chars:
        plain = plain[:content_max_chars]

    summary = plain[:500] + ("…" if len(plain) > 500 else "") if plain else None
    meta_bits = [f"r/{sub}"]
    score = post.get("score")
    if isinstance(score, int):
        meta_bits.append(f"score={score}")
    comments = post.get("num_comments")
    if isinstance(comments, int):
        meta_bits.append(f"comments={comments}")
    flair = (post.get("link_flair_text") or "").strip()
    if flair:
        meta_bits.append(f"flair={flair}")
    meta = " · ".join(meta_bits)
    summary = f"{summary}\n\n({meta})" if summary else f"({meta})"

    created = post.get("created_utc")
    published = (
        datetime.fromtimestamp(float(created), tz=UTC)
        if isinstance(created, (int, float))
        else None
    )
    source_id, source_name = subreddit_source(sub)

    payload: dict[str, Any] = {
        "title": title,
        "link": f"{REDDIT_ORIGIN}{permalink}",
        "published": published,
        "summary": summary,
        "content": plain or None,
        "source_id": source_id,
        "source_name": source_name,
        "channel": "social",
    }
    if include_raw:
        payload["raw"] = {
            "id": post.get("id"),
            "subreddit": sub,
            "author": post.get("author"),
            "score": score,
            "num_comments": comments,
            "link_flair_text": flair or None,
            "is_self": post.get("is_self"),
            "url": post.get("url"),
        }
    return payload
