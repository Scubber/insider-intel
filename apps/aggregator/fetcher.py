"""HTTP fetching for RSS/Atom feed bodies."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_USER_AGENT = "insider-intel/0.1 (+https://thederpweb.com; RSS aggregator)"


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
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }

    owns_client = client is None
    http_client = client or httpx.Client(timeout=timeout, follow_redirects=True)

    try:
        logger.debug("Fetching feed: %s", url)
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
