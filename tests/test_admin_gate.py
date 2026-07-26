"""ADMIN_API_TOKEN gate on the API's write/ops endpoints.

The public API serves an anonymous read product, but /reload (the OOM-fatal
index swap), subscription writes, and the ingest_url endpoints are levers a
stranger could abuse. When ADMIN_API_TOKEN is set they require the bearer;
unset stays open so local dev needs zero setup.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.search import service
from apps.search.api import app
from shared.settings import Settings

TOKEN = "test-admin-token"  # pragma: allowlist secret


def _client(tmp_path, monkeypatch, *, token: str | None) -> TestClient:
    path = tmp_path / "processed.jsonl"
    path.write_text("")
    kwargs = dict(
        PROCESSED_ARTICLES_PATH=str(path),
        RAW_ARTICLES_PATH=str(tmp_path / "raw.jsonl"),
        SOCIAL_SUBSCRIPTIONS_PATH=str(tmp_path / "subs.json"),
        CORS_ORIGINS="http://127.0.0.1:5500",
    )
    if token is not None:
        kwargs["ADMIN_API_TOKEN"] = token
    settings = Settings(**kwargs)
    monkeypatch.setattr("apps.search.service.get_settings", lambda: settings)
    monkeypatch.setattr("apps.search.api.get_settings", lambda: settings)
    monkeypatch.setattr(service, "_index", None)
    monkeypatch.setattr(service, "_index_path", None)
    return TestClient(app)


def test_gated_endpoints_require_bearer_when_token_set(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, token=TOKEN)
    sub = {"platform": "reddit", "id": "overemployed"}

    assert client.post("/reload").status_code == 401
    assert client.post("/social/subscriptions", json=sub).status_code == 401
    assert client.delete("/social/subscriptions/reddit/overemployed").status_code == 401
    assert (
        client.post("/social/ingest_url", json={"url": "https://reddit.com/r/x/1"}).status_code
        == 401
    )
    assert (
        client.post(
            "/publications/ingest_url", json={"url": "https://example.com/paper.pdf"}
        ).status_code
        == 401
    )

    wrong = {"Authorization": "Bearer nope"}
    assert client.post("/reload", headers=wrong).status_code == 403

    good = {"Authorization": f"Bearer {TOKEN}"}
    assert client.post("/reload", headers=good).status_code == 200
    created = client.post("/social/subscriptions", json=sub, headers=good)
    assert created.status_code == 200
    assert (
        client.delete("/social/subscriptions/reddit/overemployed", headers=good).status_code
        == 200
    )


def test_read_endpoints_stay_anonymous(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, token=TOKEN)
    assert client.get("/health").status_code == 200
    assert client.get("/articles?limit=1").status_code == 200
    assert client.get("/social/catalog").status_code == 200


def test_unset_token_keeps_endpoints_open_for_dev(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, token=None)
    assert client.post("/reload").status_code == 200
    sub = {"platform": "reddit", "id": "overemployed"}
    assert client.post("/social/subscriptions", json=sub).status_code == 200
