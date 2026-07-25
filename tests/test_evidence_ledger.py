"""GET /evidence/ledger — corpus-wide evidence aggregation for the sidebar."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search import service
from apps.search.api import app
from shared.agents import process_article
from shared.schemas import RawArticle
from shared.schemas.forensics import CaseMethod, CaseObservable, PerCaseForensics
from shared.settings import Settings
from shared.utils.evidence import build_evidence_ledger

NOW = datetime.now(UTC)


def _forensics(link: str, *, status: str) -> PerCaseForensics:
    return PerCaseForensics(
        link=link,
        title=link,
        candidate_technique_ids=["IF002"],
        methods=[
            CaseMethod(
                action="USB copy of design files",
                claim_status=status,  # type: ignore[arg-type]
                observables=[
                    CaseObservable(
                        description="mass copy to removable media",
                        artifact="EDR removable-media events",
                        channel="endpoint",
                        basis="mechanically_implied",
                    )
                ],
            )
        ],
        is_insider_case=True,
        confidence=0.9,
    )


def _client(tmp_path, monkeypatch) -> TestClient:
    raws = [
        RawArticle(
            title=f"Case {n}",
            link=f"https://ex.com/case-{n}",
            summary="Disgruntled employee used removable media after resignation.",
            content="Insider data exfiltration via USB drive by departing employee.",
            published=NOW,
            source_id="example",
            source_name="Example",
        )
        for n in range(2)
    ]
    processed = [process_article(raw) for raw in raws]
    processed[0] = processed[0].model_copy(
        update={"forensics": _forensics(processed[0].link, status="adjudicated")}
    )
    processed[1] = processed[1].model_copy(
        update={"forensics": _forensics(processed[1].link, status="alleged")}
    )
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


def test_ledger_endpoint_aggregates_corpus(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        resp = client.get("/evidence/ledger")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enriched_cases"] == 2
        # Adjudicated vs alleged never conflated.
        assert data["strength_totals"]["adjudicated_admitted"] == 1
        assert data["strength_totals"]["alleged"] == 1
        # Technique frequency includes the shared IF002.
        techs = {t["id"]: t for t in data["techniques"]}
        assert techs["IF002"]["cases"] == 2
        assert techs["IF002"]["adjudicated_admitted"] == 1
        # Detected-by normalizes the artifact into the USB family with mech count.
        arts = {a["artifact"]: a for a in data["detected_by"]}
        usb = arts["removable-media (USB) logs"]
        assert usb["cases"] == 2 and usb["adjudicated_admitted_cases"] == 1
        assert usb["mechanical_observables"] == 2
        assert data["channels"]["endpoint"] == 2

        # Bounds validated.
        assert client.get("/evidence/ledger", params={"top": 0}).status_code == 422


def test_core_handles_empty_and_malformed_rows() -> None:
    ledger = build_evidence_ledger([{}, {"forensics": None}, "not-a-dict", {"forensics": {}}])
    assert ledger["enriched_cases"] == 0
    assert ledger["techniques"] == [] and ledger["detected_by"] == []
