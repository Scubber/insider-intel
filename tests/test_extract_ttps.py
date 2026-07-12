"""Tests for POST /extract/ttps seed-floor extraction."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search import service
from apps.search.api import app
from shared.agents import process_article
from shared.schemas import RawArticle
from shared.settings import Settings


def test_extract_ttps_seed_floor_without_xai(tmp_path, monkeypatch) -> None:
    article = process_article(
        RawArticle(
            title="Employee moonlighting and undisclosed concurrent employment dispute",
            link="https://example.com/moonlighting-case",
            summary="Outside employment policy and dual employment allegations.",
            published=datetime(2024, 6, 1, tzinfo=UTC),
            source_id="example",
            source_name="Example",
        )
    )
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save([article])

    settings = Settings(
        PROCESSED_ARTICLES_PATH=str(path),
        RAW_ARTICLES_PATH=str(tmp_path / "raw.jsonl"),
        CORS_ORIGINS="http://127.0.0.1:5500",
        XAI_API_KEY=None,
    )
    monkeypatch.setattr("apps.search.service.get_settings", lambda: settings)
    monkeypatch.setattr("apps.search.api.get_settings", lambda: settings)
    service.get_index(path, reload=True)

    client = TestClient(app)
    res = client.post(
        "/extract/ttps",
        json={"links": ["https://example.com/moonlighting-case"]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == "seeds"
    assert body["article_count"] == 1
    assert body["titles"] == ["Employee moonlighting and undisclosed concurrent employment dispute"]
    assert any(b["id"] == "TTP-OE-01" for b in body["behaviors"])
    assert body["email"]
    assert body["human"]
