"""Tests for Matrix topic_match article filtering."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search import service
from apps.search.api import app
from apps.search.index import _article_matches_itm
from shared.itm.index import clear_itm_cache
from shared.schemas import ExtractedEntities, ProcessedArticle
from shared.settings import Settings
from shared.utils.entities import extract_entities, match_itm_techniques


def _article(
    *, title: str, summary: str = "", link: str = "https://example.com/x"
) -> ProcessedArticle:
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


def test_pr041_aliases_match_credential_theft() -> None:
    clear_itm_cache()
    hits = match_itm_techniques("Amazon extension flaw leads to cloud credential theft by insider")
    assert any(h.id == "PR041" for h in hits)


def test_topic_match_finds_credential_theft_without_itm_hit() -> None:
    clear_itm_cache()
    article = _article(
        title="Amazon Q VS Extension Flaw Leads to Cloud Credential Theft",
        link="https://example.com/cred-theft",
    )
    assert not any(h.id == "PR041" for h in article.entities.itm_hits)
    assert _article_matches_itm(article, itm_id="PR041", topic_match=False) is False
    assert _article_matches_itm(article, itm_id="PR041", topic_match=True) is True


def test_list_articles_topic_match_api(tmp_path, monkeypatch) -> None:
    clear_itm_cache()
    path = tmp_path / "processed.jsonl"
    store = JsonlProcessedStore(path)
    store.save(
        [
            _article(
                title="Djinn Stealer Targets Cloud, AI Credentials",
                link="https://example.com/djinn",
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
    strict = client.get(
        "/articles",
        params={
            "itm_id": "PR041",
            "itm_alignment": "all",
            "min_score": 0,
            "topic_match": False,
        },
    )
    assert strict.status_code == 200
    assert strict.json()["count"] == 0

    topic = client.get(
        "/articles",
        params={
            "itm_id": "PR041",
            "itm_alignment": "all",
            "min_score": 0,
            "topic_match": True,
        },
    )
    assert topic.status_code == 200
    body = topic.json()
    assert body["count"] >= 1
    titles = {r["title"] for r in body["results"]}
    assert any("Credential" in t or "Credentials" in t for t in titles)
    assert "Company announces quarterly earnings" not in titles


def test_generic_titles_are_denylisted() -> None:
    clear_itm_cache()
    # Words that appear incidentally in gambling-regulation / data-breach /
    # employment complaints — none should independently produce an ITM hit.
    noise = (
        "The complaint concerns online gambling and gaming platforms, alleged "
        "conflicts of interest, the browsers and firmware used by consumers, and "
        "authorized leave policies for state employees."
    )
    hits = {h.id for h in match_itm_techniques(noise)}
    for tid in ("IF008.005", "IF008.007", "MT021", "ME003.003", "ME016", "AF028.001"):
        assert tid not in hits, f"{tid} should be denylisted, got {hits}"


def test_retained_alias_still_matches_outside_employment() -> None:
    clear_itm_cache()
    hits = {
        h.id
        for h in match_itm_techniques(
            "Employee held undisclosed outside employment with a competitor while employed."
        )
    }
    assert "IF038" in hits  # kept out of the denylist on purpose


def test_extract_entities_tags_pr041_from_alias() -> None:
    clear_itm_cache()
    entities = extract_entities(
        "Insider threat case: employee used credential theft to stage cloud credentials."
    )
    assert any(h.id == "PR041" for h in entities.itm_hits)
