"""Tests for the /techniques/candidates endpoint and the export candidate view."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.aggregator.technique_seeds import TechniqueSeedStore
from apps.search.api import app
from shared.schemas.discovery import (
    CandidateCatalogResponse,
    NovelCandidate,
    SupportingCase,
)
from shared.settings import Settings


def _seed_store(path) -> None:
    store = TechniqueSeedStore(path)
    store.write(
        CandidateCatalogResponse(
            generated_at=datetime(2026, 7, 1, tzinfo=UTC),
            candidate_count=1,
            counts_by_status={"eligible": 1},
            candidates=[
                NovelCandidate(
                    id="NOVEL-abc1234567",
                    label="rclone bulk cloud sync",
                    portable_behavior="CLI cloud-sync bulk exfiltration pre-departure",
                    status="eligible",
                    flagged_for_review=True,
                    corroboration_count=2,
                    distinct_domains=2,
                    max_itm_similarity=0.31,
                    nearest_itm_id="IF002",
                    evidence_strength="strong",
                    supporting_cases=[
                        SupportingCase(link="https://a.com/1", title="Case A", story_key="sk1"),
                        SupportingCase(link="https://b.com/2", title="Case B", story_key="sk2"),
                    ],
                )
            ],
        )
    )


def test_candidates_endpoint_returns_store(tmp_path, monkeypatch) -> None:
    seeds_path = tmp_path / "seeds.json"
    _seed_store(seeds_path)
    settings = Settings(
        CORS_ORIGINS="http://127.0.0.1:5500",
        TECHNIQUE_SEEDS_PATH=str(seeds_path),
    )
    monkeypatch.setattr("apps.search.service.get_settings", lambda: settings)

    with TestClient(app) as client:
        resp = client.get("/techniques/candidates")
    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate_count"] == 1
    assert body["counts_by_status"] == {"eligible": 1}
    cand = body["candidates"][0]
    assert cand["id"] == "NOVEL-abc1234567"
    assert cand["status"] == "eligible"
    assert cand["flagged_for_review"] is True
    assert len(cand["supporting_cases"]) == 2


def test_candidates_endpoint_empty_when_no_store(tmp_path, monkeypatch) -> None:
    settings = Settings(
        CORS_ORIGINS="http://127.0.0.1:5500",
        TECHNIQUE_SEEDS_PATH=str(tmp_path / "missing.json"),
    )
    monkeypatch.setattr("apps.search.service.get_settings", lambda: settings)
    with TestClient(app) as client:
        resp = client.get("/techniques/candidates")
    assert resp.status_code == 200
    assert resp.json()["candidate_count"] == 0


def test_export_writes_candidates_ndjson(tmp_path, monkeypatch) -> None:
    from apps.aggregator import export

    seeds_path = tmp_path / "seeds.json"
    _seed_store(seeds_path)
    settings = Settings(
        CORS_ORIGINS="http://127.0.0.1:5500",
        TECHNIQUE_SEEDS_PATH=str(seeds_path),
    )
    monkeypatch.setattr("shared.settings.get_settings", lambda: settings)

    out_dir = tmp_path / "export"
    manifest = export.write_export_package(
        out_dir=out_dir,
        processed_path=tmp_path / "empty-processed.jsonl",
        itm_alignment="all",
    )
    assert manifest["schema_version"] == "insider-intel.export.v5"
    assert manifest["candidate_count"] == 1
    assert manifest["files"]["candidates"] == "candidates.ndjson"
    lines = (out_dir / "candidates.ndjson").read_text().strip().splitlines()
    assert len(lines) == 1
    assert "NOVEL-abc1234567" in lines[0]
