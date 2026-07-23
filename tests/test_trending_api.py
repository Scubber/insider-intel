"""API tests for GET /trending — most-active topics, recent vs prior window."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search import service
from apps.search.api import app
from shared.agents import process_article
from shared.schemas import RawArticle
from shared.settings import Settings

NOW = datetime.now(UTC)

OVEREMPLOYMENT_TEXT = (
    "Overemployment thread: worker secretly holds two full-time jobs, using a "
    "mouse jiggler on the company laptop while moonlighting for a second employer."
)
SABOTAGE_TEXT = (
    "Fired sysadmin sabotage: deleted virtual machines and wiped backups using a "
    "retained service account after termination."
)


def _article(title: str, link: str, published: datetime, content: str) -> RawArticle:
    return RawArticle(
        title=title,
        link=link,
        summary=content[:120],
        content=content,
        published=published,
        source_id="example",
        source_name="Example",
    )


def _client(tmp_path, monkeypatch) -> TestClient:
    # The windows anchor on processed_at (≈ test runtime), so every stamp
    # keeps half-a-day of margin from a window boundary.
    oe = OVEREMPLOYMENT_TEXT
    raws = [
        # Rising topic (7d window): 3 recent overemployment stories vs 1 prior.
        _article("OE case one", "https://ex.com/oe1", NOW - timedelta(days=1), oe),
        _article("OE case two", "https://ex.com/oe2", NOW - timedelta(days=2), oe),
        _article("OE case three", "https://ex.com/oe3", NOW - timedelta(hours=60), oe),
        _article("OE old case", "https://ex.com/oe-old", NOW - timedelta(days=10), oe),
        # New topic (7d window): recent-only sabotage stories.
        _article("Sabotage one", "https://ex.com/sab1", NOW - timedelta(days=1), SABOTAGE_TEXT),
        _article("Sabotage two", "https://ex.com/sab2", NOW - timedelta(days=2), SABOTAGE_TEXT),
        _article("Sabotage three", "https://ex.com/sab3", NOW - timedelta(days=4), SABOTAGE_TEXT),
        # Outside both windows entirely: must not count anywhere.
        _article("Ancient", "https://ex.com/ancient", NOW - timedelta(days=40), oe),
    ]
    processed = [process_article(raw) for raw in raws]
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save(processed)

    settings = Settings(
        PROCESSED_ARTICLES_PATH=str(path),
        RAW_ARTICLES_PATH=str(tmp_path / "raw.jsonl"),
        SOCIAL_SUBSCRIPTIONS_PATH=str(tmp_path / "subs.json"),
        CORS_ORIGINS="http://127.0.0.1:5500",
    )
    monkeypatch.setattr("apps.search.service.get_settings", lambda: settings)
    monkeypatch.setattr("apps.search.api.get_settings", lambda: settings)
    monkeypatch.setattr(service, "_index", None)
    monkeypatch.setattr(service, "_index_path", None)
    return TestClient(app)


def test_trending_shape_and_directions(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        resp = client.get("/trending", params={"limit": 20})
        assert resp.status_code == 200
        body = resp.json()
        assert body["window_days"] == 7
        items = body["items"]
        assert items, "expected trending topics from the seeded corpus"
        for item in items:
            assert item["kind"] in {"use_case", "technique", "term"}
            assert item["direction"] in {"up", "down", "flat", "new"}
            assert item["count"] >= 2

        # Ranked by total volume across the whole corpus, most-common first.
        counts = [i["count"] for i in items]
        assert counts == sorted(counts, reverse=True)
        assert items[0]["count"] == 5  # overemployment appears in 5 stories total

        # Overemployment: 5 total, 3 recent vs 1 prior → secondary arrow 'up'.
        assert any(
            i["count"] == 5 and i["prev_count"] == 1 and i["direction"] == "up" for i in items
        )
        # Sabotage: 3 total, recent-only → arrow 'new'.
        assert any(i["count"] == 3 and i["direction"] == "new" for i in items)


def test_trending_limit_and_window_params(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        resp = client.get("/trending", params={"limit": 2, "window_days": 7})
        assert resp.status_code == 200
        assert len(resp.json()["items"]) <= 2

        # window_days sizes only the trend arrow, not the volume ranking. A
        # 3-day window puts the day-4 sabotage story in the prior window
        # (sabotage arrow 'up', prev 1) and pushes the day-10 overemployment
        # story out of both windows (overemployment recent-only → 'new'); the
        # totals are unchanged (overemployment 5, sabotage 3).
        resp = client.get("/trending", params={"window_days": 3, "limit": 20})
        assert resp.status_code == 200
        items = resp.json()["items"]
        directions = {i["direction"] for i in items}
        assert "new" in directions and "up" in directions
        assert any(
            i["count"] == 3 and i["prev_count"] == 1 for i in items if i["direction"] == "up"
        )

        assert client.get("/trending", params={"window_days": 0}).status_code == 422
        assert client.get("/trending", params={"limit": 99}).status_code == 422


def test_trending_empty_corpus(tmp_path, monkeypatch) -> None:
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save([])
    settings = Settings(
        PROCESSED_ARTICLES_PATH=str(path),
        RAW_ARTICLES_PATH=str(tmp_path / "raw.jsonl"),
        SOCIAL_SUBSCRIPTIONS_PATH=str(tmp_path / "subs.json"),
        CORS_ORIGINS="http://127.0.0.1:5500",
    )
    monkeypatch.setattr("apps.search.service.get_settings", lambda: settings)
    monkeypatch.setattr("apps.search.api.get_settings", lambda: settings)
    monkeypatch.setattr(service, "_index", None)
    monkeypatch.setattr(service, "_index_path", None)
    with TestClient(app) as client:
        resp = client.get("/trending")
        assert resp.status_code == 200
        assert resp.json()["items"] == []
