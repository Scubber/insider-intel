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
        assert usb["mechanical_observables"] == 2 and usb["inferred_observables"] == 0
        # Real artifact strings ride along so the UI can show what the family holds.
        assert usb["examples"] == ["EDR removable-media events"]
        assert data["channels"]["endpoint"] == 2

        # Bounds validated.
        assert client.get("/evidence/ledger", params={"top": 0}).status_code == 422


def test_core_handles_empty_and_malformed_rows() -> None:
    ledger = build_evidence_ledger([{}, {"forensics": None}, "not-a-dict", {"forensics": {}}])
    assert ledger["enriched_cases"] == 0
    assert ledger["techniques"] == [] and ledger["detected_by"] == []


def test_role_normalizer_two_axes() -> None:
    from shared.utils.evidence import normalize_role

    assert normalize_role("departing engineer — file share") == ("technical", "departing")
    assert normalize_role("fired former employee") == ("unknown", "former/fired")
    assert normalize_role("Chief Financial Officer") == ("executive/officer", "current")
    assert normalize_role("third-party consultant") == ("contractor/vendor", "third-party")
    assert normalize_role("") == ("unknown", "unknown")


def test_ledger_themes_roles_and_small_n(tmp_path, monkeypatch) -> None:
    from shared.utils.evidence import SMALL_N_FLOOR

    with _client(tmp_path, monkeypatch) as client:
        data = client.get("/evidence/ledger").json()
        # Theme grouping: IF002 lands under infringement, with a theme rollup.
        techs = {t["id"]: t for t in data["techniques"]}
        assert techs["IF002"]["theme"] == "infringement"
        themes = {t["theme"]: t for t in data["themes"]}
        assert themes["infringement"]["cases"] == 2
        # Corroboration: DT ids stamped onto techniques (catalog join), and the
        # USB family corroborates the USB-artifact detections when present.
        assert "detections" in techs["IF002"]
        assert "corroboration" in data
        # Roles axis present with small-n suppression (2 cases < floor → no %).
        assert data["small_n_floor"] == SMALL_N_FLOOR
        fn = {r["label"]: r for r in data["roles"]["function"]}
        assert all(r["share_pct"] is None for r in fn.values())  # n=2 < 10
        # per-technique top families ride along for the page.
        assert techs["IF002"]["top_families"][0]["artifact"] == "removable-media (USB) logs"


def test_evidence_technique_endpoint(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        resp = client.get("/evidence/technique/if002")
        assert resp.status_code == 200
        d = resp.json()
        assert d["id"] == "IF002" and d["theme"] == "infringement"
        assert d["cases"] == 2 and d["adjudicated_admitted"] == 1
        assert d["evidence"][0]["artifact"] == "removable-media (USB) logs"
        assert isinstance(d["detections"], list)
        # Unobserved technique → 404, not an empty body.
        assert client.get("/evidence/technique/ZZ999").status_code == 404


def test_crosswalk_ids_exist_in_catalog() -> None:
    """Every DT id in the crosswalk must be a real catalog detection."""
    from shared.itm.index import load_itm_index
    from shared.utils.evidence import EVIDENCE_DT_CROSSWALK

    catalog_ids = {d.id for tech in load_itm_index().techniques for d in tech.detections}
    if not catalog_ids:  # packaged index missing in some environments
        return
    mapped = {dt for dts in EVIDENCE_DT_CROSSWALK.values() for dt in dts}
    missing = mapped - catalog_ids
    assert not missing, f"crosswalk references unknown detections: {sorted(missing)}"
