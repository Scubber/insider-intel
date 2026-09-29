"""HTTP fetching for RSS/Atom feed bodies."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_USER_AGENT = "insider-intel/0.1 (+https://thederpweb.com; RSS aggregator)"
# WAF fallback: some feed hosts (Security Boulevard's 403 streak, 2026-09) reject
# any non-browser User-Agent outright. The polite bot UA stays the first attempt
# so hosts that log or rate-limit by agent see who we are; a 403 earns exactly one
# retry with a browser-style UA before the lane reports the failure.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class FeedFetchError(Exception):
    """Raised when a feed cannot be fetched."""

    def __init__(self, url: str, message: str) -> None:
        self.url = url
        self.message = message
        super().__init__(f"{url}: {message}")


def fetch_feed(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    user_agent: str = DEFAULT_USER_AGENT,
    client: httpx.Client | None = None,
) -> str:
    """Download feed content as text.

    Args:
        url: Feed URL.
        timeout: Request timeout in seconds.
        user_agent: User-Agent header value.
        client: Optional shared httpx client (caller owns lifecycle).

    Returns:
        Response body as text.

    Raises:
        FeedFetchError: On network, HTTP, or empty-body failures.
    """
    accept = "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"

    owns_client = client is None
    http_client = client or httpx.Client(timeout=timeout, follow_redirects=True)

    try:
        logger.debug("Fetching feed: %s", url)
        headers = {"User-Agent": user_agent, "Accept": accept}
        response = http_client.get(url, headers=headers)
        if response.status_code == 403 and user_agent != BROWSER_USER_AGENT:
            logger.info("Feed %s returned 403 to %r; retrying with browser UA", url, user_agent)
            headers["User-Agent"] = BROWSER_USER_AGENT
            response = http_client.get(url, headers=headers)
        response.raise_for_status()
        body = response.text
        if not body or not body.strip():
            raise FeedFetchError(url, "empty response body")
        return body
    except FeedFetchError:
        raise
    except httpx.HTTPStatusError as exc:
        raise FeedFetchError(url, f"HTTP {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        raise FeedFetchError(url, f"request failed: {exc}") from exc
    finally:
        if owns_client:
            http_client.close()
