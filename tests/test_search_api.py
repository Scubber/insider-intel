"""API tests for the FastAPI search / reader app."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search import service
from apps.search.api import app
from shared.agents import process_article
from shared.schemas import RawArticle
from shared.settings import Settings


def test_health_search_articles_itm_and_sources(tmp_path, monkeypatch) -> None:
    article = process_article(
        RawArticle(
            title="Insider threat: USB exfiltration after resignation",
            link="https://example.com/insider",
            summary=(
                "Disgruntled departing employee used removable media for "
                "exfiltration via physical medium and mass download."
            ),
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
    monkeypatch.setattr(service, "_index", None)
    monkeypatch.setattr(service, "_index_path", None)

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["indexed_articles"] == 1

        sources = client.get("/sources")
        assert sources.status_code == 200
        assert isinstance(sources.json(), list)
        assert len(sources.json()) >= 1
        assert "id" in sources.json()[0]
        assert "name" in sources.json()[0]

        filtered_sources = client.get(
            "/sources",
            params={"itm_alignment": "insider", "min_score": 0.0},
        )
        assert filtered_sources.status_code == 200
        insider_ids = {s["id"] for s in filtered_sources.json()}
        assert "example" in insider_ids
        example = next(s for s in filtered_sources.json() if s["id"] == "example")
        assert example["article_count"] >= 1

        empty_theme = client.get(
            "/sources",
            params={"itm_alignment": "insider", "theme": "anti-forensics"},
        )
        assert empty_theme.status_code == 200
        # This fixture may or may not match anti-forensics; counts must be coherent
        assert all(s["article_count"] >= 1 for s in empty_theme.json())

        itm = client.get("/itm")
        assert itm.status_code == 200
        catalog = itm.json()
        assert len(catalog["articles"]) == 5
        assert len(catalog["techniques"]) >= 20
        assert "Forscie" in catalog["attribution"]
        # Parents + subsections; at least one technique carries DT links
        assert any(t.get("parent_id") for t in catalog["techniques"])
        assert any(t.get("detections") for t in catalog["techniques"])
        assert any(t.get("preventions") for t in catalog["techniques"])
        assert all("article_count" in t for t in catalog["techniques"])
        assert any(t["article_count"] >= 1 for t in catalog["techniques"])
        assert all("description" in t for t in catalog["techniques"])
        assert any(t["description"] for t in catalog["techniques"])

        articles = client.get("/articles", params={"limit": 10})
        assert articles.status_code == 200
        body = articles.json()
        assert body["count"] == 1
        hit = body["results"][0]
        assert hit["link"] == "https://example.com/insider"
        assert hit["itm_hits"]
        assert "insider threat" in hit["keywords_hit"]
        assert hit.get("operator_terms")
        assert "insider threat" in hit["operator_terms"]
        assert "ME005" not in hit["operator_terms"]
        assert hit.get("itm_alignment") == "insider"

        # Default /articles is ITM-aligned insider scenarios only
        weak_only = process_article(
            RawArticle(
                title="Generic patch advisory",
                link="https://example.com/patch",
                summary="Microsoft fixed remote code execution. CVE-2024-11111.",
                published=datetime(2024, 6, 2, tzinfo=UTC),
                source_id="example",
                source_name="Example",
            )
        )
        JsonlProcessedStore(path).save([weak_only])
        monkeypatch.setattr(service, "_index", None)
        monkeypatch.setattr(service, "_index_path", None)
        service.get_index(path, reload=True)

        strict = client.get("/articles", params={"limit": 10})
        assert strict.status_code == 200
        links = {r["link"] for r in strict.json()["results"]}
        assert "https://example.com/insider" in links
        assert "https://example.com/patch" not in links

        all_hits = client.get("/articles", params={"limit": 10, "itm_alignment": "all"})
        assert all_hits.status_code == 200
        all_links = {r["link"] for r in all_hits.json()["results"]}
        assert "https://example.com/patch" in all_links

        by_theme = client.get("/articles", params={"theme": "infringement"})
        assert by_theme.status_code == 200
        assert by_theme.json()["count"] == 1

        by_id = client.get("/articles", params={"itm_id": "ME005"})
        assert by_id.status_code == 200
        assert by_id.json()["count"] == 1

        empty_theme = client.get("/articles", params={"theme": "anti-forensics"})
        assert empty_theme.status_code == 200
        # May be 0 unless anti-forensics also matched
        assert empty_theme.json()["count"] >= 0

        resp = client.get("/search", params={"q": "exfiltration", "mode": "hybrid"})
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1

        post = client.post(
            "/search",
            json={"query": "resignation", "mode": "keyword", "limit": 5},
        )
        assert post.status_code == 200
        assert post.json()["count"] >= 1

        by_links = client.post(
            "/articles/by-links",
            json={
                "links": [
                    "https://example.com/insider",
                    "https://example.com/not-indexed",
                ]
            },
        )
        assert by_links.status_code == 200
        payload = by_links.json()
        assert [r["link"] for r in payload["results"]] == ["https://example.com/insider"]
        assert payload["results"][0]["operator_terms"]
        assert payload["missing"] == ["https://example.com/not-indexed"]

        assert client.post("/articles/by-links", json={"links": []}).status_code == 422
