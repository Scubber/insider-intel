"""API tests for social filters, /usecases, and /social endpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search import service
from apps.search.api import app
from shared.agents import process_article
from shared.schemas import RawArticle
from shared.settings import Settings


def _client(tmp_path, monkeypatch) -> TestClient:
    oe_post = process_article(
        RawArticle(
            title="Working two remote jobs without telling either employer",
            link="https://www.reddit.com/r/jobsearchhacks/comments/abc123/post/",
            summary=(
                "I've been overemployed for a year, J1 and J2. Our outside "
                "employment policy forbids it but everyone does it."
            ),
            published=datetime(2026, 7, 10, tzinfo=UTC),
            source_id="social-reddit-jobsearchhacks",
            source_name="Reddit r/jobsearchhacks",
            channel="social",
        )
    )
    news = process_article(
        RawArticle(
            title="Insider threat: USB exfiltration after resignation",
            link="https://example.com/insider",
            summary=(
                "Disgruntled departing employee stole data using removable media "
                "for exfiltration."
            ),
            published=datetime(2026, 7, 9, tzinfo=UTC),
            source_id="example",
            source_name="Example",
        )
    )
    path = tmp_path / "processed.jsonl"
    JsonlProcessedStore(path).save([oe_post, news])

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


def test_channel_use_case_and_insider_type_filters(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        social = client.get(
            "/articles", params={"channel": "social", "itm_alignment": "all"}
        )
        assert social.status_code == 200
        links = {r["link"] for r in social.json()["results"]}
        assert links == {"https://www.reddit.com/r/jobsearchhacks/comments/abc123/post/"}

        oe = client.get(
            "/articles", params={"use_case": "overemployment", "itm_alignment": "all"}
        )
        assert oe.status_code == 200
        hits = oe.json()["results"]
        assert len(hits) == 1
        assert "overemployment" in hits[0]["use_cases"]
        assert hits[0]["insider_type"] == "negligent"

        malicious = client.get(
            "/articles", params={"insider_type": "malicious", "itm_alignment": "all"}
        )
        assert malicious.status_code == 200
        assert {r["link"] for r in malicious.json()["results"]} == {
            "https://example.com/insider"
        }

        none_match = client.get(
            "/articles",
            params={
                "use_case": "shadow-it",
                "insider_type": "unintentional",
                "itm_alignment": "all",
            },
        )
        assert none_match.json()["count"] == 0

        search = client.get(
            "/search",
            params={
                "q": "two jobs",
                "itm_alignment": "all",
                "insider_type": "negligent",
            },
        )
        assert search.status_code == 200
        assert search.json()["count"] == 1

        sources = client.get(
            "/sources", params={"use_case": "overemployment", "itm_alignment": "all"}
        )
        assert sources.status_code == 200
        ids = {s["id"] for s in sources.json()}
        assert ids == {"social-reddit-jobsearchhacks"}


def test_legacy_rows_without_classification_fields(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        settings_path = None
        # Strip the new fields from every stored row to simulate old JSONL.
        for candidate in tmp_path.glob("processed.jsonl"):
            settings_path = candidate
        assert settings_path is not None
        rows = [
            json.loads(line)
            for line in settings_path.read_text(encoding="utf-8").splitlines()
        ]
        for row in rows:
            for key in (
                "use_cases",
                "insider_type",
                "classification_source",
                "classification_confidence",
            ):
                row.pop(key, None)
        settings_path.write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )
        assert client.post("/reload").status_code == 200

        everything = client.get("/articles", params={"itm_alignment": "all"})
        assert everything.json()["count"] == 2

        oe = client.get(
            "/articles", params={"use_case": "overemployment", "itm_alignment": "all"}
        )
        assert oe.json()["count"] == 0  # unclassified until process --force

        unclassified = client.get(
            "/articles", params={"insider_type": "none", "itm_alignment": "all"}
        )
        assert unclassified.json()["count"] == 2


def test_usecases_endpoint(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        resp = client.get("/usecases")
        assert resp.status_code == 200
        ids = [u["id"] for u in resp.json()]
        assert ids == [
            "overemployment",
            "data-exfiltration",
            "credential-misuse",
            "shadow-it",
        ]
        assert all(u["label"] for u in resp.json())


def test_social_catalog_and_subscriptions(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        catalog = client.get("/social/catalog")
        assert catalog.status_code == 200
        body = catalog.json()
        suggested_ids = {s["id"] for s in body["suggestions"]}
        assert {"overemployed", "jobsearchhacks"} <= suggested_ids
        assert body["subscriptions"] == []
        jsh = next(s for s in body["suggestions"] if s["id"] == "jobsearchhacks")
        assert jsh["article_count"] == 1  # indexed social post counts

        added = client.post(
            "/social/subscriptions",
            json={"platform": "reddit", "id": "r/Overemployed"},
        )
        assert added.status_code == 200
        assert added.json()["id"] == "overemployed"
        assert added.json()["origin"] == "catalog"
        assert "overemployment" in added.json()["use_cases"]

        subs = client.get("/social/subscriptions")
        assert [s["id"] for s in subs.json()] == ["overemployed"]

        catalog2 = client.get("/social/catalog").json()
        oe = next(s for s in catalog2["suggestions"] if s["id"] == "overemployed")
        assert oe["subscribed"] is True

        removed = client.delete("/social/subscriptions/reddit/overemployed")
        assert removed.status_code == 200
        assert client.get("/social/subscriptions").json() == []
        assert (
            client.delete("/social/subscriptions/reddit/overemployed").status_code
            == 404
        )
        assert client.delete("/social/subscriptions/facebook/foo").status_code == 400
