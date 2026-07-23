"""GET /feed.xml — public Atom feed of the flagged insider-threat stream."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search import service
from apps.search.api import app
from shared.agents import process_article
from shared.schemas import RawArticle
from shared.settings import Settings

ATOM = "{http://www.w3.org/2005/Atom}"
NOW = datetime.now(UTC)

OE_TEXT = (
    "Overemployment: worker secretly holds two full-time jobs using a mouse "
    "jiggler on the company laptop while moonlighting for a second employer."
)
SABOTAGE_TEXT = (
    "Fired sysadmin sabotage: deleted virtual machines and wiped backups using "
    "a retained service account after termination."
)


def _article(title: str, link: str, published: datetime, content: str) -> RawArticle:
    return RawArticle(
        title=title,
        link=link,
        summary=content[:100],
        content=content,
        published=published,
        source_id="example",
        source_name="Example",
    )


def _client(tmp_path, monkeypatch) -> TestClient:
    raws = [
        _article("OE case one", "https://ex.com/oe1", NOW - timedelta(days=1), OE_TEXT),
        _article("OE case two", "https://ex.com/oe2", NOW - timedelta(days=2), OE_TEXT),
        _article("Sabotage one", "https://ex.com/sab1", NOW - timedelta(days=1), SABOTAGE_TEXT),
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


def test_feed_is_valid_atom(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        resp = client.get("/feed.xml", params={"itm_alignment": "all"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/atom+xml")

        root = ET.fromstring(resp.text)  # parseable == well-formed
        assert root.tag == f"{ATOM}feed"
        assert root.find(f"{ATOM}title") is not None
        assert root.find(f"{ATOM}updated") is not None
        assert root.find(f"{ATOM}id") is not None

        entries = root.findall(f"{ATOM}entry")
        assert entries, "expected feed entries from the seeded corpus"
        for entry in entries:
            assert entry.find(f"{ATOM}title") is not None
            assert entry.find(f"{ATOM}id").text.startswith("https://")
            assert entry.find(f"{ATOM}link").get("href", "").startswith("https://")
            assert entry.find(f"{ATOM}updated") is not None


def test_feed_honors_facets(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        overall = client.get("/feed.xml", params={"itm_alignment": "all"})
        oe = client.get("/feed.xml", params={"itm_alignment": "all", "use_case": "overemployment"})
        assert overall.status_code == oe.status_code == 200

        all_entries = ET.fromstring(overall.text).findall(f"{ATOM}entry")
        oe_entries = ET.fromstring(oe.text).findall(f"{ATOM}entry")
        # The overemployment facet is a strict subset of the full stream.
        assert 0 < len(oe_entries) <= len(all_entries)
        for entry in oe_entries:
            terms = {c.get("term") for c in entry.findall(f"{ATOM}category")}
            assert "overemployment" in terms


def test_feed_validation_bounds(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/feed.xml", params={"limit": 0}).status_code == 422
        assert client.get("/feed.xml", params={"limit": 999}).status_code == 422
