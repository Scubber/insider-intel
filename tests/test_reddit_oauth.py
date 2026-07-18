"""Reddit OAuth app auth: token caching, 401 re-mint, path selection."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import apps.aggregator.reddit as reddit
from apps.aggregator.reddit import fetch_subreddit_new

LISTING = json.loads((Path(__file__).parent / "fixtures" / "reddit_new_listing.json").read_text())


@pytest.fixture(autouse=True)
def _fresh_token_cache():
    reddit._clear_app_token()
    yield
    reddit._clear_app_token()


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_oauth_listing_uses_bearer_and_caches_token() -> None:
    calls = {"token": 0, "listing": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/access_token":
            calls["token"] += 1
            assert request.headers["Authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
        calls["listing"] += 1
        assert request.url.host == "oauth.reddit.com"
        assert request.headers["Authorization"] == "bearer tok-1"
        assert request.url.path == "/r/jobsearchhacks/new"
        return httpx.Response(200, json=LISTING)

    client = _client(handler)
    for _ in range(2):
        posts = fetch_subreddit_new(
            "jobsearchhacks",
            client_id="cid",
            client_secret="sec",  # pragma: allowlist secret
            client=client,
        )
        assert posts and posts[0]["subreddit"] == "jobsearchhacks"
    assert calls["token"] == 1  # cached across calls
    assert calls["listing"] == 2


def test_oauth_401_mints_fresh_token_once() -> None:
    calls = {"token": 0, "listing": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/access_token":
            calls["token"] += 1
            return httpx.Response(
                200, json={"access_token": f"tok-{calls['token']}", "expires_in": 3600}
            )
        calls["listing"] += 1
        if request.headers["Authorization"] == "bearer tok-1":
            return httpx.Response(401)
        return httpx.Response(200, json=LISTING)

    posts = fetch_subreddit_new(
        "jobsearchhacks",
        client_id="cid",
        client_secret="sec",  # pragma: allowlist secret
        client=_client(handler),
    )
    assert posts
    assert calls["token"] == 2
    assert calls["listing"] == 2


def test_no_creds_falls_back_to_public_json(monkeypatch) -> None:
    seen = {}

    def fake_get_json(url, **kwargs):
        seen["url"] = url
        return LISTING

    monkeypatch.setattr(reddit, "_get_json", fake_get_json)
    posts = fetch_subreddit_new("jobsearchhacks")
    assert posts
    assert seen["url"] == "https://www.reddit.com/r/jobsearchhacks/new.json"
