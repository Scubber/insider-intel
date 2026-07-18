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


def test_extract_ttps_seed_floor_overemployment(tmp_path, monkeypatch) -> None:
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
    assert body["matched_if038"] is True


def _processed(title: str, link: str, summary: str):
    return process_article(
        RawArticle(
            title=title,
            link=link,
            summary=summary,
            published=datetime(2024, 6, 1, tzinfo=UTC),
            source_id="example",
            source_name="Example",
        )
    )


def test_seed_floor_without_if038_reports_actual_techniques() -> None:
    from apps.search.ttp_extract import seed_floor_report

    article = _processed(
        "Insider threat: engineer exfiltrated trade secrets",
        "https://example.com/exfil-case",
        "Disgruntled employee used removable media for data exfiltration.",
    )
    assert article.entities.itm_hits
    assert not any(h.id.upper() == "IF038" for h in article.entities.itm_hits)

    report = seed_floor_report([article])
    assert report.matched_if038 is False
    ids = {b.id for b in report.behaviors}
    # No overemployment placebo pack — behaviors come from the article's hits.
    assert not any(i.startswith("TTP-OE") for i in ids)
    assert {h.id for h in article.entities.itm_hits} & ids


def test_seed_floor_case_record_feeds_behaviors_and_seeds() -> None:
    from apps.search.ttp_extract import seed_floor_report
    from shared.schemas import CaseRecord

    article = _processed(
        "Insider threat: engineer exfiltrated trade secrets",
        "https://example.com/exfil-case-2",
        "Disgruntled employee used removable media for data exfiltration.",
    ).model_copy(
        update={
            "case_record": CaseRecord(
                is_insider_case=True,
                actor_role="departing engineer",
                methods=["rclone sync to personal cloud"],
                exfil_channels=["personal Gmail"],
                detection_trigger="DLP alert on outbound mail",
            )
        }
    )
    report = seed_floor_report([article])
    assert any(b.id.startswith("CASE-") for b in report.behaviors)
    assert "rclone sync to personal cloud" in report.seeds
    assert "personal Gmailil" not in report.seeds  # sanity: exact strings only
    assert "personal Gmail" in report.network
    assert "DLP alert on outbound mail" in report.human


def test_articles_text_endpoint_returns_filing_body(tmp_path, monkeypatch) -> None:
    from apps.aggregator.storage import JsonlArticleStore

    body = "The defendant exfiltrated schematics to a personal drive."
    raw = RawArticle(
        title="United States v. Example",
        link="https://www.courtlistener.com/docket/9/us-v-example/",
        summary="Court: SDNY",
        content=f"CourtListener query: insider\n--- Document 1: Complaint ---\n{body}",
        source_id="courtlistener-recap",
        source_name="CourtListener RECAP",
        channel="filings",
    )
    filing = process_article(raw)
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save([filing])
    raw_path = tmp_path / "raw.jsonl"
    JsonlArticleStore(raw_path).save([raw])
    settings = Settings(
        PROCESSED_ARTICLES_PATH=str(path),
        RAW_ARTICLES_PATH=str(raw_path),
        CORS_ORIGINS="http://127.0.0.1:5500",
    )
    monkeypatch.setattr("apps.search.service.get_settings", lambda: settings)
    monkeypatch.setattr("apps.search.api.get_settings", lambda: settings)
    service.get_index(path, reload=True)

    client = TestClient(app)
    res = client.get("/articles/text", params={"link": filing.link})
    assert res.status_code == 200
    data = res.json()
    assert data["channel"] == "filings"
    assert body in data["text"]
    assert "CourtListener query:" not in data["text"]
    assert "--- Document 1: Complaint ---" in data["text"]  # raw line breaks kept

    missing = client.get("/articles/text", params={"link": "https://example.com/nope"})
    assert missing.status_code == 404
