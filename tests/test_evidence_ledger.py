"""GET /evidence/ledger — corpus-wide evidence aggregation for the sidebar."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.search import service
from apps.search.api import app
from shared.agents import process_article
from shared.schemas import RawArticle
from shared.schemas.forensics import CaseMethod, CaseObservable, HuntQuerySeed, PerCaseForensics
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
        hunt_terms=[
            "removable media",
            "mass file copy",
            "resignation window",
            # Case-specific entities — must be filtered out of the aggregation.
            "Holly Hill Logistics",
            "Robert Dawson",
            "@holcim.com",
        ],
        hunt_queries=[
            HuntQuerySeed(
                stack="EDR",
                logic="removable_media_write AND file_count > 100 within 24h of resignation",
                rationale="Mass USB copy in the departure window",
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
        TECHNIQUE_HUNTS_PATH=str(tmp_path / "hunts.json"),
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
        # Technique names are spelled out for the UI (joined from the catalog).
        assert techs["IF002"].get("title") and techs["IF002"]["title"] != "IF002"
        # Detected-by normalizes the artifact into the USB family with mech count.
        arts = {a["artifact"]: a for a in data["detected_by"]}
        usb = arts["removable-media (USB) logs"]
        assert usb["cases"] == 2 and usb["adjudicated_admitted_cases"] == 1
        assert usb["mechanical_observables"] == 2 and usb["inferred_observables"] == 0
        # Real artifact strings ride along so the UI can show what the family holds.
        assert usb["examples"] == ["EDR removable-media events"]
        assert data["channels"]["endpoint"] == 2
        # Internal per-technique maps stay off the corpus-wide payload.
        assert "technique_hunts" not in data

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
        # Case-derived hunt seeds: deduped by logic (both cases share one),
        # attributed to a case, adjudicated first.
        assert len(d["hunts"]) == 1
        hunt = d["hunts"][0]
        assert hunt["stack"] == "EDR" and "removable_media_write" in hunt["logic"]
        assert hunt["case"] and hunt["strength"] == "adjudicated/admitted"
        # Case-found search terms ride along (deduped across cases) so the UI
        # can compose an LLM hunt prompt — with case-specific entities
        # (companies, people, domains) filtered out.
        assert d["terms"] == ["removable media", "mass file copy", "resignation window"]
        # Observed behaviors (method actions) ride along for the prompt too.
        assert d["behaviors"][0]["action"] == "USB copy of design files"
        assert d["behaviors"][0]["strength"] == "adjudicated/admitted"
        # Synthesized patterns absent until the refresh job writes the view.
        assert d["patterns"] == [] and d["patterns_generated_at"] is None
        # Unobserved technique → 404, not an empty body.
        assert client.get("/evidence/technique/ZZ999").status_code == 404


def test_technique_endpoint_serves_synthesized_patterns(tmp_path, monkeypatch) -> None:
    from apps.aggregator.hunt_synthesis import TechniqueHuntStore
    from shared.schemas.hunt_patterns import HuntPattern, TechniqueHuntEntry

    with _client(tmp_path, monkeypatch) as client:
        TechniqueHuntStore(tmp_path / "hunts.json").write(
            {
                "IF002": TechniqueHuntEntry(
                    technique_id="IF002",
                    signature="abc",
                    generated_at="2026-08-08T00:00:00+00:00",
                    model="test-model",
                    case_count=2,
                    patterns=[
                        HuntPattern(
                            name="Departure-window bulk copy",
                            who_class="departing employees",
                            detect=["Review departing employees' file transfers"],
                            prevent=["Revoke access at resignation notice"],
                        )
                    ],
                )
            }
        )
        d = client.get("/evidence/technique/IF002").json()
        assert d["patterns"][0]["name"] == "Departure-window bulk copy"
        assert d["patterns_generated_at"] == "2026-08-08T00:00:00+00:00"


def test_entity_term_filter() -> None:
    from shared.utils.evidence import is_entity_term

    # Case-specific entities → dropped.
    for t in [
        "Holly Hill Logistics",
        "Cornerstone Resources Group",
        "@holcim.com",
        "Robert Dawson",
        "TFE Games Holdings LLC",
        "Intrepid Studios Inc",
        "Ashes of Creation",
        "Settlement Agreement May 2024",
    ]:
        assert is_entity_term(t), t
    # Behavior indicators → kept.
    for t in [
        "personal email account",
        "WhatsApp",
        "Git repository access",
        "P4 repository access",
        "backend credentials",
        "Visa Business credit card",
        "Article 9 foreclosure",
        "removable media",
    ]:
        assert not is_entity_term(t), t


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
