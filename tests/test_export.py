"""Tests for one-way corporate export package and API."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.aggregator.export import (
    EXPORT_SCHEMA_VERSION,
    article_to_export_row,
    write_export_package,
)
from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search import service
from apps.search.api import app
from shared.agents import process_article
from shared.schemas import RawArticle
from shared.settings import Settings


def _sample_processed():
    return process_article(
        RawArticle(
            title="Insider threat: USB exfiltration after resignation",
            link="https://example.com/insider-export",
            summary=(
                "Disgruntled departing employee used removable media for "
                "exfiltration via physical medium and mass download."
            ),
            published=datetime(2024, 6, 1, tzinfo=UTC),
            source_id="example",
            source_name="Example",
        )
    )


def test_article_to_export_row_shape() -> None:
    row = article_to_export_row(_sample_processed())
    assert row["title"].startswith("Insider threat")
    assert row["link"].startswith("https://")
    assert row["itm_alignment"] == "insider"
    assert "operator_terms" in row
    assert "itm_hits" in row
    assert "related_detections" in row
    assert "related_preventions" in row
    assert row["related_detections"], "export should include DT* handoff"
    assert row["related_preventions"], "export should include PV* handoff"
    assert "keywords_hit" in row
    assert "cves" in row
    assert "domains" in row


def test_write_export_package(tmp_path) -> None:
    processed = tmp_path / "processed.jsonl"
    JsonlProcessedStore(processed).save([_sample_processed()])
    out = tmp_path / "export"
    manifest = write_export_package(
        out_dir=out,
        processed_path=processed,
        min_score=0.0,
        itm_alignment="insider",
    )
    assert manifest["schema_version"] == EXPORT_SCHEMA_VERSION
    assert manifest["article_count"] == 1
    assert manifest["itm_alignment"] == "insider"
    ndjson = (out / "articles.ndjson").read_text(encoding="utf-8").strip()
    assert "insider-export" in ndjson
    assert (out / "manifest.json").exists()


def test_export_api_requires_token(tmp_path, monkeypatch) -> None:
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save([_sample_processed()])
    settings = Settings(
        PROCESSED_ARTICLES_PATH=str(path),
        RAW_ARTICLES_PATH=str(tmp_path / "raw.jsonl"),
        EXPORT_API_TOKEN="test-export-secret",
        CORS_ORIGINS="http://127.0.0.1:5500",
    )
    monkeypatch.setattr("apps.search.service.get_settings", lambda: settings)
    monkeypatch.setattr("apps.search.api.get_settings", lambda: settings)
    monkeypatch.setattr(service, "_index", None)
    monkeypatch.setattr(service, "_index_path", None)

    with TestClient(app) as client:
        denied = client.get("/export/articles")
        assert denied.status_code == 401

        bad = client.get(
            "/export/articles",
            headers={"Authorization": "Bearer wrong"},
        )
        assert bad.status_code == 403

        ok = client.get(
            "/export/articles",
            headers={"Authorization": "Bearer test-export-secret"},
        )
        assert ok.status_code == 200
        body = ok.json()
        assert body["schema_version"] == EXPORT_SCHEMA_VERSION
        assert body["count"] == 1
        assert body["results"][0]["link"] == "https://example.com/insider-export"
