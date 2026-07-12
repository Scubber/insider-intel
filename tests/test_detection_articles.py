"""Tests for detection/prevention reverse-join article filters."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search import service
from apps.search.api import app
from apps.search.index import _article_matches_itm
from shared.itm.controls import (
    list_detection_catalog,
    techniques_for_detection,
)
from shared.itm.index import clear_itm_cache
from shared.schemas import ExtractedEntities, ProcessedArticle
from shared.settings import Settings


def _article(*, title: str, summary: str = "", link: str) -> ProcessedArticle:
    return ProcessedArticle(
        title=title,
        link=link,
        summary=summary or title,
        clean_text=f"{title}. {summary}".strip(),
        source_id="example",
        source_name="Example",
        published=datetime(2024, 6, 1, tzinfo=UTC),
        entities=ExtractedEntities(),
        relevance_score=0.5,
        itm_alignment="weak",
    )


def test_techniques_for_detection_usbstor() -> None:
    clear_itm_cache()
    tech_ids = techniques_for_detection("DT021")
    assert tech_ids
    assert any(tid.startswith("ME005") or tid.startswith("IF002") for tid in tech_ids)


def test_detection_catalog_on_itm_endpoint(tmp_path, monkeypatch) -> None:
    clear_itm_cache()
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save([])
    settings = Settings(
        PROCESSED_ARTICLES_PATH=str(path),
        RAW_ARTICLES_PATH=str(tmp_path / "raw.jsonl"),
        CORS_ORIGINS="http://127.0.0.1:5500",
    )
    monkeypatch.setattr("apps.search.service.get_settings", lambda: settings)
    monkeypatch.setattr("apps.search.api.get_settings", lambda: settings)
    monkeypatch.setattr(service, "_index", None)
    monkeypatch.setattr(service, "_index_path", None)

    client = TestClient(app)
    catalog = client.get("/itm").json()
    assert len(catalog["detections"]) >= 50
    assert len(catalog["preventions"]) >= 20
    assert any(d["id"] == "DT021" for d in catalog["detections"])


def test_articles_by_detection_id_topic_match(tmp_path, monkeypatch) -> None:
    clear_itm_cache()
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save(
        [
            _article(
                title="Employee copies files to USB removable media before resignation",
                link="https://example.com/usb",
            ),
            _article(
                title="Company announces quarterly earnings",
                link="https://example.com/earnings",
            ),
        ]
    )
    settings = Settings(
        PROCESSED_ARTICLES_PATH=str(path),
        RAW_ARTICLES_PATH=str(tmp_path / "raw.jsonl"),
        CORS_ORIGINS="http://127.0.0.1:5500",
    )
    monkeypatch.setattr("apps.search.service.get_settings", lambda: settings)
    monkeypatch.setattr("apps.search.api.get_settings", lambda: settings)
    monkeypatch.setattr(service, "_index", None)
    monkeypatch.setattr(service, "_index_path", None)

    client = TestClient(app)
    body = client.get(
        "/articles",
        params={
            "detection_id": "DT021",
            "itm_alignment": "all",
            "min_score": 0,
            "topic_match": True,
        },
    ).json()
    assert body["count"] >= 1
    titles = {r["title"] for r in body["results"]}
    assert any("USB" in t or "removable" in t.lower() for t in titles)
    assert "Company announces quarterly earnings" not in titles


def test_article_matches_detection_without_prior_hit() -> None:
    clear_itm_cache()
    article = _article(
        title="USBSTOR registry shows mass removable media use",
        link="https://example.com/usbstor",
        summary="Removable media exfiltration via physical medium.",
    )
    assert _article_matches_itm(article, detection_id="DT021", topic_match=False) is False
    assert _article_matches_itm(article, detection_id="DT021", topic_match=True) is True
    assert list_detection_catalog()
