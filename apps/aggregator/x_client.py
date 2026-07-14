"""X (Twitter) API v2 client — thin, bearer-token gated.

Read access requires a paid API tier; when X_BEARER_TOKEN is unset the
pipeline degrades gracefully instead of failing the run.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from apps.aggregator.social_subscriptions import normalize_handle, social_source_id

logger = logging.getLogger(__name__)

API_ORIGIN = "https://api.twitter.com"
DEFAULT_USER_ID_CACHE = "data/state/x_user_ids.json"


def handle_source(handle: str) -> tuple[str, str]:
    """(source_id, source_name) for an X handle."""
    normalized = normalize_handle("x", handle)
    return social_source_id("x", normalized), f"X @{normalized}"


class XClient:
    def __init__(self, *, bearer_token: str, timeout: float = 20.0,
                 user_id_cache_path: str | Path = DEFAULT_USER_ID_CACHE) -> None:
        self._headers = {"Authorization": f"Bearer {bearer_token}"}
        self._timeout = timeout
        self._cache_path = Path(user_id_cache_path)

    def _load_cache(self) -> dict[str, str]:
        if not self._cache_path.exists():
            return {}
        try:
            return json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}

    def _save_cache(self, cache: dict[str, str]) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._cache_path)

    def user_id(self, handle: str) -> str:
        normalized = normalize_handle("x", handle)
        cache = self._load_cache()
        if normalized in cache:
            return cache[normalized]
        response = httpx.get(
            f"{API_ORIGIN}/2/users/by/username/{normalized}",
            headers=self._headers,
            timeout=self._timeout,
        )
        response.raise_for_status()
        user_id = response.json()["data"]["id"]
        cache[normalized] = user_id
        self._save_cache(cache)
        return user_id

    def recent_tweets(self, handle: str, *, max_results: int = 25) -> list[dict[str, Any]]:
        user_id = self.user_id(handle)
        response = httpx.get(
            f"{API_ORIGIN}/2/users/{user_id}/tweets",
            headers=self._headers,
            timeout=self._timeout,
            params={
                "max_results": min(max(max_results, 5), 100),
                "tweet.fields": "created_at,public_metrics",
                "exclude": "retweets,replies",
            },
        )
        response.raise_for_status()
        return response.json().get("data") or []


def tweet_to_article(
    tweet: dict[str, Any],
    handle: str,
    *,
    include_raw: bool = False,
) -> dict[str, Any] | None:
    """Map a v2 tweet object to RawArticle field kwargs."""
    text = (tweet.get("text") or "").strip()
    tweet_id = (tweet.get("id") or "").strip()
    if not text or not tweet_id:
        return None
    normalized = normalize_handle("x", handle)
    title = text[:120] + ("…" if len(text) > 120 else "")
    published = None
    created = tweet.get("created_at")
    if created:
        try:
            published = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            published = None
    source_id, source_name = handle_source(normalized)
    payload: dict[str, Any] = {
        "title": title,
        "link": f"https://x.com/{normalized}/status/{tweet_id}",
        "published": published,
        "summary": text[:500],
        "content": text,
        "source_id": source_id,
        "source_name": source_name,
        "channel": "social",
    }
    if include_raw:
        payload["raw"] = {
            "id": tweet_id,
            "handle": normalized,
            "public_metrics": tweet.get("public_metrics"),
        }
    return payload
