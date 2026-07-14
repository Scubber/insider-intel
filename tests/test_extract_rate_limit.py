"""Rate limiting for POST /extract/ttps (paid-LLM endpoint)."""

from __future__ import annotations

from fastapi.testclient import TestClient

import apps.search.api as api_module
from apps.search.ratelimit import SlidingWindowLimiter
from apps.search.ttp_extract import ExtractTtpsResponse
from shared.settings import Settings


def test_per_ip_window_expires() -> None:
    limiter = SlidingWindowLimiter(per_ip_per_hour=2, global_per_day=100)
    assert limiter.allow("1.2.3.4", now=0.0)
    assert limiter.allow("1.2.3.4", now=1.0)
    assert not limiter.allow("1.2.3.4", now=2.0)
    # other IPs unaffected
    assert limiter.allow("5.6.7.8", now=2.0)
    # window slides: first hit ages out after an hour
    assert limiter.allow("1.2.3.4", now=3601.5)


def test_global_cap() -> None:
    limiter = SlidingWindowLimiter(per_ip_per_hour=100, global_per_day=3)
    assert limiter.allow("a", now=0.0)
    assert limiter.allow("b", now=0.0)
    assert limiter.allow("c", now=0.0)
    assert not limiter.allow("d", now=0.0)


def test_extract_endpoint_returns_429(tmp_path, monkeypatch) -> None:
    settings = Settings(
        PROCESSED_ARTICLES_PATH=str(tmp_path / "processed.jsonl"),
        RAW_ARTICLES_PATH=str(tmp_path / "raw.jsonl"),
    )
    monkeypatch.setattr("apps.search.service.get_settings", lambda: settings)
    monkeypatch.setattr("apps.search.api.get_settings", lambda: settings)
    monkeypatch.setattr(
        api_module,
        "_extract_limiter",
        SlidingWindowLimiter(per_ip_per_hour=2, global_per_day=100),
    )
    monkeypatch.setattr(
        api_module,
        "extract_ttps_for_links",
        lambda index, links, settings: ExtractTtpsResponse(mode="seeds", article_count=0),
    )
    client = TestClient(api_module.app)
    body = {"links": ["https://example.com/a"]}
    assert client.post("/extract/ttps", json=body).status_code == 200
    assert client.post("/extract/ttps", json=body).status_code == 200
    assert client.post("/extract/ttps", json=body).status_code == 429
