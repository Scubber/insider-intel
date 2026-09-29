"""fetch_feed contract: polite UA first, one browser-UA retry on a WAF 403."""

from __future__ import annotations

import httpx
import pytest

from apps.aggregator.fetcher import (
    BROWSER_USER_AGENT,
    DEFAULT_USER_AGENT,
    FeedFetchError,
    fetch_feed,
)

FEED_URL = "https://feeds.example.com/feed/"
RSS_BODY = "<rss><channel><title>ok</title></channel></rss>"


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_success_uses_bot_agent_and_fetches_once():
    seen = []

    def handler(request):
        seen.append(request.headers["User-Agent"])
        return httpx.Response(200, text=RSS_BODY)

    with _client(handler) as client:
        assert fetch_feed(FEED_URL, client=client) == RSS_BODY
    assert seen == [DEFAULT_USER_AGENT]


def test_403_retries_once_with_browser_agent():
    seen = []

    def handler(request):
        seen.append(request.headers["User-Agent"])
        if request.headers["User-Agent"] == BROWSER_USER_AGENT:
            return httpx.Response(200, text=RSS_BODY)
        return httpx.Response(403, text="blocked")

    with _client(handler) as client:
        assert fetch_feed(FEED_URL, client=client) == RSS_BODY
    assert seen == [DEFAULT_USER_AGENT, BROWSER_USER_AGENT]


def test_403_on_both_agents_reports_403():
    seen = []

    def handler(request):
        seen.append(request.headers["User-Agent"])
        return httpx.Response(403, text="blocked")

    with _client(handler) as client:
        with pytest.raises(FeedFetchError, match="HTTP 403"):
            fetch_feed(FEED_URL, client=client)
    assert seen == [DEFAULT_USER_AGENT, BROWSER_USER_AGENT]


def test_non_403_error_fails_without_retry():
    seen = []

    def handler(request):
        seen.append(request.headers["User-Agent"])
        return httpx.Response(404, text="gone")

    with _client(handler) as client:
        with pytest.raises(FeedFetchError, match="HTTP 404"):
            fetch_feed(FEED_URL, client=client)
    assert seen == [DEFAULT_USER_AGENT]


def test_caller_pinned_browser_agent_does_not_double_send():
    seen = []

    def handler(request):
        seen.append(request.headers["User-Agent"])
        return httpx.Response(403, text="blocked")

    with _client(handler) as client:
        with pytest.raises(FeedFetchError, match="HTTP 403"):
            fetch_feed(FEED_URL, user_agent=BROWSER_USER_AGENT, client=client)
    assert seen == [BROWSER_USER_AGENT]
