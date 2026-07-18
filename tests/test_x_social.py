"""X ingestion: token gating, tweet mapping, pipeline behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import apps.aggregator.x_pipeline as x_pipeline
from apps.aggregator.x_client import handle_source, tweet_to_article

FIXTURE = Path(__file__).parent / "fixtures" / "x_user_tweets.json"


def _tweets() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]


def test_tweet_to_article_mapping() -> None:
    fields = tweet_to_article(_tweets()[0], "@ThreatWire", include_raw=True)
    assert fields is not None
    assert fields["source_id"] == "social-x-threatwire"
    assert fields["source_name"] == "X @threatwire"
    assert fields["channel"] == "social"
    assert fields["link"] == "https://x.com/threatwire/status/1801234567890123456"
    assert fields["title"].endswith("…")
    assert fields["published"] is not None
    assert fields["raw"]["public_metrics"]["like_count"] == 240


def test_handle_source() -> None:
    assert handle_source("@Overemployed_") == ("social-x-overemployed_", "X @overemployed_")


def test_unconfigured_token_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    result = x_pipeline.run_x_ingestion(
        handles=["threatwire"],
        store_path=str(tmp_path / "raw.jsonl"),
    )
    assert result.sources == []
    assert result.total_articles_saved == 0


def test_pipeline_with_stub_client(tmp_path: Path) -> None:
    class StubClient:
        def recent_tweets(self, handle: str, *, max_results: int = 25):
            return _tweets()

    result = x_pipeline.run_x_ingestion(
        handles=["threatwire"],
        store_path=str(tmp_path / "raw.jsonl"),
        client=StubClient(),  # type: ignore[arg-type]
    )
    assert len(result.sources) == 1
    assert result.sources[0].success
    assert result.total_articles_saved == 2


def test_pipeline_isolates_api_errors(tmp_path: Path) -> None:
    class FailingClient:
        def recent_tweets(self, handle: str, *, max_results: int = 25):
            raise RuntimeError("HTTP 403 payment required")

    result = x_pipeline.run_x_ingestion(
        handles=["threatwire"],
        store_path=str(tmp_path / "raw.jsonl"),
        client=FailingClient(),  # type: ignore[arg-type]
    )
    assert len(result.sources) == 1
    assert not result.sources[0].success
    assert "403" in (result.sources[0].error or "")


def test_mint_bearer_token_caches(tmp_path: Path) -> None:
    import httpx

    from apps.aggregator.x_client import mint_bearer_token

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.path == "/oauth2/token"
        assert request.headers.get("Authorization", "").startswith("Basic ")
        return httpx.Response(200, text=json.dumps({"access_token": "AAAA-token"}))

    cache = tmp_path / "x_bearer.json"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert mint_bearer_token("ck", "cs", cache_path=cache, client=client) == "AAAA-token"
        # Second call served from the cache — no extra HTTP.
        assert mint_bearer_token("ck", "cs", cache_path=cache, client=client) == "AAAA-token"
    assert len(calls) == 1


def test_consumer_pair_mints_bearer_for_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("X_CONSUMER_KEY", "ck")
    monkeypatch.setenv("X_CONSUMER_SECRET", "cs")
    minted: list[tuple[str, str]] = []

    def fake_mint(key, secret, **kw):
        minted.append((key, secret))
        return "minted-bearer"

    monkeypatch.setattr(x_pipeline, "mint_bearer_token", fake_mint)

    captured: dict[str, str] = {}

    class CapturingXClient:
        def __init__(self, *, bearer_token: str):
            captured["bearer"] = bearer_token

        def recent_tweets(self, handle: str, *, max_results: int = 25):
            return _tweets()

    monkeypatch.setattr(x_pipeline, "XClient", CapturingXClient)
    result = x_pipeline.run_x_ingestion(
        handles=["threatwire"],
        store_path=str(tmp_path / "raw.jsonl"),
    )
    assert minted == [("ck", "cs")]
    assert captured["bearer"] == "minted-bearer"
    assert result.total_articles_saved == 2


def test_cadence_gate_skips_recent_runs(tmp_path: Path) -> None:
    from apps.aggregator.ingest_state import JsonIngestState

    class StubClient:
        calls = 0

        def recent_tweets(self, handle: str, *, max_results: int = 25):
            StubClient.calls += 1
            return _tweets()

    state = JsonIngestState(tmp_path / "state.json")
    kwargs = dict(
        handles=["threatwire"],
        store_path=str(tmp_path / "raw.jsonl"),
        client=StubClient(),
        state=state,
    )
    first = x_pipeline.run_x_ingestion(**kwargs)  # type: ignore[arg-type]
    assert first.total_articles_saved == 2 and StubClient.calls == 1

    # Immediately again → inside the 48h window, skipped entirely.
    second = x_pipeline.run_x_ingestion(**kwargs)  # type: ignore[arg-type]
    assert second.sources == [] and StubClient.calls == 1

    # Without state (manual CLI/test path) the cadence does not apply.
    third = x_pipeline.run_x_ingestion(
        handles=["threatwire"],
        store_path=str(tmp_path / "raw.jsonl"),
        client=StubClient(),  # type: ignore[arg-type]
    )
    assert StubClient.calls == 2 and third.sources
