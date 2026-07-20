"""API tests for the publications channel facet and /publications/ingest_url."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search import service
from apps.search.api import app
from shared.agents import process_article
from shared.schemas import RawArticle
from shared.settings import Settings

PUB_LINK = "https://pubs.example.org/library/common-sense-guide/"


def _client(tmp_path, monkeypatch) -> TestClient:
    pub = process_article(
        RawArticle(
            title="Common Sense Guide to Mitigating Insider Threats",
            link=PUB_LINK,
            summary="Best practices for insider threat mitigation.",
            content=(
                "Insider threat programs should monitor data exfiltration via "
                "removable media and privileged access misuse by employees."
            ),
            published=datetime(2026, 7, 1, tzinfo=UTC),
            source_id="pub-sei-common-sense-guide-7e",
            source_name="SEI Common Sense Guide",
            channel="publications",
        )
    )
    news = process_article(
        RawArticle(
            title="Insider threat: USB exfiltration after resignation",
            link="https://example.com/insider",
            summary="Departing employee stole data using removable media.",
            published=datetime(2026, 7, 9, tzinfo=UTC),
            source_id="example",
            source_name="Example",
        )
    )
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save([pub, news])

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


def test_publications_channel_filter(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        pubs = client.get("/articles", params={"channel": "publications", "itm_alignment": "all"})
        assert pubs.status_code == 200
        assert {r["link"] for r in pubs.json()["results"]} == {PUB_LINK}
        assert pubs.json()["results"][0]["channel"] == "publications"

        news = client.get("/articles", params={"channel": "news", "itm_alignment": "all"})
        assert PUB_LINK not in {r["link"] for r in news.json()["results"]}

        sources = client.get("/sources", params={"channel": "publications", "itm_alignment": "all"})
        assert sources.status_code == 200
        assert {s["id"] for s in sources.json()} == {"pub-sei-common-sense-guide-7e"}


def test_publications_ingest_url_endpoint(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        new_link = "https://pubs.example.org/new-guide/"

        def fake_ingest(url, *, store_path):
            article = RawArticle(
                title="New Insider Threat Guide",
                link=url,
                summary="Guide summary.",
                content="Employees exfiltrated data using removable media and email.",
                source_id="pub-pubs-example-org",
                source_name="pubs.example.org",
                channel="publications",
            )
            from apps.aggregator.storage import JsonlArticleStore

            JsonlArticleStore(store_path).refresh([article])
            return article

        monkeypatch.setattr(
            "apps.aggregator.publications_pipeline.ingest_publication_url",
            fake_ingest,
        )
        resp = client.post("/publications/ingest_url", json={"url": new_link})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ingested"
        assert body["channel"] == "publications"
        assert body["content_chars"] > 0

        pubs = client.get("/articles", params={"channel": "publications", "itm_alignment": "all"})
        assert new_link in {r["link"] for r in pubs.json()["results"]}


def test_publications_ingest_url_fetch_failure_maps_502(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:

        def boom(url, *, store_path):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(
            "apps.aggregator.publications_pipeline.ingest_publication_url",
            boom,
        )
        resp = client.post("/publications/ingest_url", json={"url": "https://blocked.example/doc"})
        assert resp.status_code == 502


def test_stream_min_score_floor_exempts_publications(tmp_path, monkeypatch) -> None:
    """The UI's High-signal floor must never hide curated reference docs."""
    with _client(tmp_path, monkeypatch) as client:
        resp = client.get("/articles", params={"min_score": 0.99, "itm_alignment": "all"})
        assert resp.status_code == 200
        links = {r["link"] for r in resp.json()["results"]}
        assert PUB_LINK in links  # publication passes despite low score
        assert "https://example.com/insider" not in links  # news gated

        sources = client.get("/sources", params={"min_score": 0.99, "itm_alignment": "all"})
        assert {s["id"] for s in sources.json()} == {"pub-sei-common-sense-guide-7e"}
