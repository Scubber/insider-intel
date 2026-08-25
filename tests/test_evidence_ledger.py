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
        # Staleness stamp + verdict-gated basis thread through to the client.
        assert data["generated_at"]
        assert data["basis"]["contributing_cases"] == 2
        assert data["basis"]["excluded_non_insider"] == 0
        assert "posture" in data and "quote_grounding" in data

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


# ---------------------------------------------------------------------------
# Verdict gate / posture cap / staleness stamp / quote grounding (2026-08-16
# audit fixes) — synthetic dict rows straight into the aggregation core.
# ---------------------------------------------------------------------------


def _method(
    action: str = "USB copy of design files",
    *,
    claim: str = "adjudicated",
    quote: str = "",
    verbatim: bool | None = None,
) -> dict:
    return {
        "action": action,
        "claim_status": claim,
        "evidence_quote": quote,
        "evidence_quote_verbatim": verbatim,
        "observables": [
            {
                "description": "mass copy to removable media",
                "artifact": "EDR removable-media events",
                "channel": "endpoint",
                "basis": "mechanically_implied",
            }
        ],
    }


def _row(
    link: str,
    *,
    insider: bool | None = True,
    methods: list[dict] | None = None,
    posture: str = "unknown",
    model: str = "model-a",
) -> dict:
    forensics = {
        "legal_posture": posture,
        "model": model,
        "candidate_technique_ids": ["IF002"],
        "methods": [_method()] if methods is None else methods,
    }
    if insider is not None:
        forensics["is_insider_case"] = insider
    return {"link": link, "title": link, "published": "2024-03-01", "forensics": forensics}


def test_non_insider_rows_excluded_from_insider_aggregates() -> None:
    """D-contamination: adjudicated-NOT-insider rows contribute nothing."""
    ledger = build_evidence_ledger(
        [
            _row("in-1", insider=True),
            _row("in-2", insider=True),
            _row("out-1", insider=False),  # enriched, method-bearing, non-insider
        ]
    )
    assert ledger["total_rows"] == 3
    assert ledger["enriched_cases"] == 2  # gate applied
    techs = {t["id"]: t for t in ledger["techniques"]}
    assert techs["IF002"]["cases"] == 2
    assert ledger["channels"]["endpoint"] == 2
    arts = {a["artifact"]: a for a in ledger["detected_by"]}
    assert arts["removable-media (USB) logs"]["cases"] == 2
    assert sum(ledger["strength_totals"].values()) == 2
    assert ledger["basis"] == {
        "corpus_rows": 3,
        "enriched_rows": 3,
        "verdict_true_rows": 2,
        "contributing_cases": 2,
        "excluded_non_insider": 1,
        "excluded_no_verdict": 0,
        "model_mix": {"model-a": 2},
    }


def test_missing_or_none_verdict_excluded() -> None:
    """Uncertainty is not evidence: a row with no verdict fails the gate."""
    ledger = build_evidence_ledger(
        [
            _row("in-1", insider=True),
            _row("unadjudicated", insider=None),  # no is_insider_case field at all
            {
                "link": "null-verdict",
                "title": "null-verdict",
                "published": "2024-03-01",
                "forensics": {"is_insider_case": None, "methods": [_method()]},
            },
        ]
    )
    assert ledger["enriched_cases"] == 1
    assert ledger["basis"]["excluded_no_verdict"] == 2
    assert ledger["basis"]["excluded_non_insider"] == 0
    assert ledger["basis"]["verdict_true_rows"] == 1
    techs = {t["id"]: t for t in ledger["techniques"]}
    assert techs["IF002"]["cases"] == 1


def test_posture_weight_ordering_constant() -> None:
    """Documented ordering: adjudicated/plea > settlement > indictment > complaint."""
    from shared.utils.evidence import POSTURE_ADJUDICATED_MIN_WEIGHT, POSTURE_WEIGHT

    assert (
        POSTURE_WEIGHT["conviction"]
        > POSTURE_WEIGHT["plea"]
        > POSTURE_WEIGHT["settlement"]
        > POSTURE_WEIGHT["indictment"]
        > POSTURE_WEIGHT["complaint"]
    )
    assert POSTURE_WEIGHT["sentencing"] >= POSTURE_ADJUDICATED_MIN_WEIGHT
    assert POSTURE_WEIGHT["civil_suit"] == POSTURE_WEIGHT["complaint"]
    # Missing signal never caps: none/unknown deliberately absent.
    assert "none" not in POSTURE_WEIGHT and "unknown" not in POSTURE_WEIGHT


def test_case_strength_capped_by_posture() -> None:
    from shared.utils.evidence import case_strength

    adjudicated = [{"claim_status": "adjudicated"}]
    alleged = [{"claim_status": "alleged"}]
    # Adjudicated-grade postures let a court finding stand.
    assert case_strength(adjudicated, "conviction") == "adjudicated/admitted"
    assert case_strength(adjudicated, "plea") == "adjudicated/admitted"
    # Allegation-stage documents can never mint an adjudicated case.
    assert case_strength(adjudicated, "indictment") == "alleged"
    assert case_strength(adjudicated, "complaint") == "alleged"
    assert case_strength(adjudicated, "civil_suit") == "alleged"
    assert case_strength(adjudicated, "settlement") == "alleged"
    # No posture signal → claim_status stands (legacy rows don't degrade).
    assert case_strength(adjudicated, "unknown") == "adjudicated/admitted"
    assert case_strength(adjudicated) == "adjudicated/admitted"
    # Posture never PROMOTES a weaker claim.
    assert case_strength(alleged, "conviction") == "alleged"
    assert case_strength([{"claim_status": "reported"}], "conviction") == "reported/unclear"


def test_ledger_applies_posture_cap_and_reports_it() -> None:
    ledger = build_evidence_ledger(
        [
            _row("conv", posture="conviction"),  # adjudicated claim, stands
            _row("compl", posture="complaint"),  # adjudicated claim, capped
            _row("legacy", posture="unknown"),  # no signal, stands
        ]
    )
    s = ledger["strength_totals"]
    assert s["adjudicated_admitted"] == 2 and s["alleged"] == 1
    assert ledger["posture"]["capped_cases"] == 1
    assert ledger["posture"]["mix"] == {"complaint": 1, "conviction": 1, "unknown": 1}
    techs = {t["id"]: t for t in ledger["techniques"]}
    assert techs["IF002"]["adjudicated_admitted"] == 2 and techs["IF002"]["alleged"] == 1


def test_staleness_stamp_deterministic() -> None:
    from datetime import UTC, datetime

    fixed = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)
    rows = [
        _row("in-1", insider=True, model="model-a"),
        _row("in-2", insider=True, model="model-b"),
        _row("out", insider=False),
        {"link": "raw", "title": "raw", "published": "", "forensics": None},  # unenriched
    ]
    ledger = build_evidence_ledger(rows, now=fixed)
    assert ledger["generated_at"] == "2026-08-16T12:00:00+00:00"
    b = ledger["basis"]
    assert b["corpus_rows"] == 4 and b["enriched_rows"] == 3
    assert b["verdict_true_rows"] == 2 and b["contributing_cases"] == 2
    assert b["model_mix"] == {"model-a": 1, "model-b": 1}
    # Same rows, same now → identical payload (deterministic export).
    assert build_evidence_ledger(rows, now=fixed) == ledger


def test_verbatim_quote_preference_and_share() -> None:
    methods = [
        _method("emailed files to personal account", claim="alleged"),  # no quote
        _method(
            "synced files to Dropbox",
            claim="alleged",
            quote="synced roughly 9,700 files to a personal Dropbox",
            verbatim=True,
        ),
        _method(
            "wiped his laptop",
            claim="alleged",
            quote="he definitely wiped everything",
            verbatim=False,  # paraphrase/fabrication — must never surface
        ),
        _method(
            "printed customer lists",
            claim="alleged",
            quote="printed the full customer list",
            verbatim=None,  # legacy: quote claimed, never stamped
        ),
    ]
    ledger = build_evidence_ledger([_row("case", methods=methods)])
    assert ledger["quote_grounding"] == {
        "quoted_methods": 3,
        "verbatim_true": 1,
        "verbatim_false": 1,
        "unstamped": 1,
        "verbatim_share_pct": 33,
    }
    behaviors = ledger["technique_behaviors"]["IF002"]
    # Verbatim-True behavior sorts first within equal strength; only its quote
    # is surfaced — False/unstamped quotes are withheld entirely.
    assert behaviors[0]["action"] == "synced files to Dropbox"
    assert behaviors[0]["quote"] == "synced roughly 9,700 files to a personal Dropbox"
    assert all(b["quote"] is None for b in behaviors[1:])


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


# ---------------------------------------------------------------------------
# Derived findings (the EVIDENCE headline cards)
#
# These replaced web/findings.json, whose numbers froze on the day they were
# authored. The contract these pin: never state a claim the corpus is too thin
# to support, never conflate proven with alleged, and never depend on anything
# the API layer strips before serving.
# ---------------------------------------------------------------------------

_BANNED_TELLS = (
    "delve",
    "leverage",
    "robust",
    "comprehensive",
    "seamless",
    "holistic",
    "landscape",
    "utilize",
    "it's important to note",
    "in today's world",
)


def _synthetic_rows(count: int = 60, *, year: str = "2024") -> list[dict]:
    """A corpus large enough to clear the small-n floor, with a mixed record."""
    rows = []
    for n in range(count):
        proven = n % 4 == 0
        rows.append(
            {
                "link": f"https://ex.com/s{n}",
                "title": f"Synthetic case {n}",
                "published": f"{year}-05-06T00:00:00+00:00",
                "source_id": "courtlistener-test",
                "forensics": {
                    "is_insider_case": True,
                    "model": "test-model",
                    "legal_posture": "judgment" if proven else "complaint",
                    "actor_role": "chief financial officer",
                    "candidate_technique_ids": ["IF016"],
                    "methods": [
                        {
                            "action": "wired funds to a personal account",
                            "claim_status": "adjudicated" if proven else "alleged",
                            "observables": [
                                {
                                    "description": "payment record",
                                    "artifact": "corporate email",
                                    "channel": "email",
                                    "basis": "mechanically_implied",
                                }
                            ],
                        }
                    ],
                },
            }
        )
    return rows


def test_findings_empty_below_small_n_floor(tmp_path, monkeypatch) -> None:
    """The headline assertion: too little data states nothing at all.

    The two-case fixture is well under the floor, so an empty list — not a
    hedged card built on n=2 — is the correct answer.
    """
    with _client(tmp_path, monkeypatch) as client:
        data = client.get("/evidence/ledger").json()
        assert data["enriched_cases"] == 2
        assert data["findings"] == []


def test_findings_emit_above_floor() -> None:
    from shared.utils.evidence import build_evidence_ledger

    ledger = build_evidence_ledger(_synthetic_rows(), now=NOW)
    findings = {f["id"]: f for f in ledger["findings"]}
    assert "proof-gap" in findings, "the proven-vs-alleged card must survive"
    gap = findings["proof-gap"]
    # Ranks are stamped in order and every card carries its own basis.
    assert [f["rank"] for f in ledger["findings"]] == list(range(1, len(ledger["findings"]) + 1))
    assert gap["basis"]["floor"] == 10
    assert gap["basis"]["n"] == ledger["enriched_cases"]
    assert gap["recommendations"] and gap["method"]


def test_findings_never_conflate_proven_and_alleged() -> None:
    """The proof-gap stat comes from adjudicated_admitted alone."""
    from shared.utils.evidence import build_evidence_ledger

    ledger = build_evidence_ledger(_synthetic_rows(), now=NOW)
    gap = next(f for f in ledger["findings"] if f["id"] == "proof-gap")
    totals = ledger["strength_totals"]
    cases = totals["adjudicated_admitted"] + totals["alleged"] + totals["reported_unclear"]
    assert gap["stat"] == f"{round(100 * totals['adjudicated_admitted'] / cases)}%"
    assert str(totals["adjudicated_admitted"]) in gap["takeaway"]


def test_findings_are_deterministic() -> None:
    """Same rows in, byte-identical cards out — no randomness, no clock."""
    from shared.utils.evidence import build_evidence_ledger

    rows = _synthetic_rows()
    assert (
        build_evidence_ledger(rows, now=NOW)["findings"]
        == (build_evidence_ledger(rows, now=NOW)["findings"])
    )


def test_findings_voice_bar() -> None:
    """The automatable half of the house voice rule (CLAUDE.md)."""
    from shared.utils.evidence import build_evidence_ledger

    ledger = build_evidence_ledger(_synthetic_rows(), now=NOW)
    assert ledger["findings"], "need cards to check the prose of"
    for finding in ledger["findings"]:
        blob = " ".join(
            [finding["title"], finding["takeaway"], finding["method"], *finding["recommendations"]]
        )
        for tell in _BANNED_TELLS:
            assert tell not in blob.lower(), f"{finding['id']} uses a banned tell: {tell}"
        # Numbers get verbs, and every card explains how it was counted.
        assert finding["method"].strip()
        assert finding["stat_label"].strip()
        assert finding["recommendations"], f"{finding['id']} gives the reader nothing to do"


def test_findings_survive_the_service_layer_pops() -> None:
    """apps.search.service.evidence_ledger strips the per-technique maps.

    A rule built on those would work in the CLI report and vanish from the API.
    This pins that the served payload still carries intact cards.
    """
    from shared.utils.evidence import build_evidence_ledger, derive_findings

    popped = (
        "technique_families",
        "technique_counts",
        "technique_hunts",
        "technique_terms",
        "technique_behaviors",
    )
    core = build_evidence_ledger(_synthetic_rows(), now=NOW)
    assert core["findings"]
    for finding in core["findings"]:
        assert set(finding) >= {"id", "rank", "stat", "takeaway", "method", "basis"}

    # Re-derive against a ledger with those maps already stripped: identical
    # cards prove no rule reads one.
    stripped = {k: v for k, v in core.items() if k not in popped}
    assert derive_findings(stripped) == core["findings"]


def test_by_year_uses_a_stable_technique_set() -> None:
    """A zero must mean zero, not "ranked sixth that year".

    Per-year top-N truncation used to drop a technique out of a year purely on
    rank, which a trend surface reads as a decline that never happened.
    """
    from shared.utils.evidence import build_evidence_ledger

    rows = _synthetic_rows(30, year="2023") + _synthetic_rows(30, year="2024")
    # A second technique present only in 2024.
    extra = _synthetic_rows(12, year="2024")
    for row in extra:
        row["link"] += "-x"
        row["forensics"]["candidate_technique_ids"] = ["IF002"]
    ledger = build_evidence_ledger(rows + extra, now=NOW)
    trend = ledger["trend_techniques"]
    assert "IF002" in trend and "IF016" in trend
    # Every year reports every tracked technique, explicitly zero when absent.
    for year, bucket in ledger["by_year"].items():
        assert set(bucket["techniques"]) == set(trend), f"{year} lost a tracked technique"
    assert ledger["by_year"]["2023"]["techniques"]["IF002"] == 0
    assert ledger["by_year"]["2024"]["techniques"]["IF002"] == 12
    assert ledger["by_year"]["2023"]["cases"] == 30


def test_by_year_excludes_undated_rows() -> None:
    """Rows with no usable date are reported as a count, never drawn as a year."""
    from shared.utils.evidence import build_evidence_ledger

    rows = _synthetic_rows(20)
    for row in rows[:5]:
        row["published"] = ""
    ledger = build_evidence_ledger(rows, now=NOW)
    assert "????" not in ledger["by_year"]
    assert ledger["by_year_undated_cases"] == 5


def test_rising_technique_skips_the_partial_current_year() -> None:
    """Comparing into a half-finished year manufactures a decline."""
    from shared.utils.evidence import build_evidence_ledger

    current = str(NOW.year)
    rows = _synthetic_rows(30, year=str(NOW.year - 1)) + _synthetic_rows(12, year=current)
    for row in rows[-12:]:
        row["link"] += "-cur"
    ledger = build_evidence_ledger(rows, now=NOW)
    rising = [f for f in ledger["findings"] if f["id"] == "rising-technique"]
    for finding in rising:
        assert current not in finding["stat_label"], "compared into the partial year"


def test_evidence_core_loads_standalone_with_findings() -> None:
    """scripts/evidence_ledger.py loads this module as a bare FILE, not a package.

    The bare Actions runner has no pydantic, so a relative import or a
    shared.* import anywhere in evidence.py breaks the ledger workflow. This
    fails the moment someone adds one.
    """
    import importlib.util
    from pathlib import Path

    core_path = Path(__file__).resolve().parent.parent / "shared" / "utils" / "evidence.py"
    spec = importlib.util.spec_from_file_location("evidence_core_standalone", core_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.derive_findings)
    ledger = module.build_evidence_ledger(_synthetic_rows(), now=NOW)
    assert ledger["findings"]


def test_by_year_titles_joined_on_the_endpoint(tmp_path, monkeypatch) -> None:
    """The core stays catalog-free, so the service joins human titles on."""
    with _client(tmp_path, monkeypatch) as client:
        data = client.get("/evidence/ledger").json()
        assert data["by_year"], "the fixture rows carry a published date"
        bucket = next(iter(data["by_year"].values()))
        assert isinstance(bucket["techniques"], list)
        entry = next(t for t in bucket["techniques"] if t["id"] == "IF002")
        assert entry["title"] and entry["title"] != "IF002"
        assert entry["cases"] == 2
        assert "findings" in data


def test_cli_report_renders_findings_and_the_trend_table() -> None:
    """scripts/evidence_ledger.py renders the same ledger the API serves.

    The by_year reshape broke this renderer once; it consumes the raw core
    payload (no catalog join), so it has to keep working on that shape.
    """
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "evidence_ledger.py"
    spec = importlib.util.spec_from_file_location("evidence_ledger_cli", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    ledger = cli.build_evidence_ledger(_synthetic_rows(), top=25)
    report = cli.render_markdown(ledger)
    assert "## Findings" in report
    assert "Most cases here are still allegations" in report
    # The appendix reads the stable-set shape without blowing up.
    assert "| Year | Cases | Tracked techniques |" in report


# ---------------------------------------------------------------------------
# Finding groups — the collapsible section's contract
# ---------------------------------------------------------------------------


def test_every_rule_names_a_real_group() -> None:
    """A rule pointing at a group id that does not exist would silently vanish
    from the grouped view while still appearing in the flat list."""
    from shared.utils.evidence import FINDING_GROUPS, build_evidence_ledger

    known = {gid for gid, _, _ in FINDING_GROUPS}
    ledger = build_evidence_ledger(_synthetic_rows(), now=NOW)
    assert ledger["findings"], "need findings to check the grouping of"
    for finding in ledger["findings"]:
        assert finding["group"] in known, f"{finding['id']} names unknown group"


def test_proof_standard_leads_so_the_honesty_card_is_never_collapsed() -> None:
    """Group one is the group the page opens on load.

    proof-gap is the never-conflate card. If a later edit reorders the groups
    and demotes it, the page opens without ever showing that proven and alleged
    are counted separately — which is the one thing this product must say.
    """
    from shared.utils.evidence import FINDING_GROUPS, _finding_proof_gap

    assert FINDING_GROUPS[0][0] == "proof-standard"
    ledger_like = {
        "enriched_cases": 60,
        "strength_totals": {"adjudicated_admitted": 12, "alleged": 48, "reported_unclear": 0},
    }
    assert _finding_proof_gap(ledger_like, 10)["group"] == "proof-standard"


def test_groups_render_in_taxonomy_order_and_carry_a_collapsed_lead() -> None:
    from shared.utils.evidence import FINDING_GROUPS, build_evidence_ledger

    ledger = build_evidence_ledger(_synthetic_rows(), now=NOW)
    groups = ledger["finding_groups"]
    assert groups, "a corpus above the floor must produce at least one group"
    order = [gid for gid, _, _ in FINDING_GROUPS]
    assert [g["id"] for g in groups] == [g for g in order if g in {x["id"] for x in groups}]
    for group in groups:
        assert group["label"] and group["blurb"]
        members = [f for f in ledger["findings"] if f["group"] == group["id"]]
        assert group["count"] == len(members)
        # A collapsed header still teaches: it carries its leading stat.
        assert group["lead"].startswith(members[0]["stat"])


def test_empty_groups_are_omitted_not_rendered_hollow() -> None:
    """An empty collapsed header advertises content that does not exist."""
    from shared.utils.evidence import build_evidence_ledger

    ledger = build_evidence_ledger(_synthetic_rows(), now=NOW)
    rendered = {g["id"] for g in ledger["finding_groups"]}
    represented = {f["group"] for f in ledger["findings"]}
    assert rendered == represented
    assert all(g["count"] > 0 for g in ledger["finding_groups"])


def test_per_group_cap_replaces_the_global_limit() -> None:
    """The old global cap of 4 existed to keep the section short. Collapsed
    groups cost no space, so the cap is per group and a rule that fired is no
    longer silently dropped because three unrelated rules fired first."""
    import shared.utils.evidence as ev

    assert not hasattr(ev, "FINDINGS_LIMIT"), "global cap should be gone"
    assert ev.FINDINGS_PER_GROUP == 3
    ledger = ev.build_evidence_ledger(_synthetic_rows(), now=NOW)
    for group in ledger["finding_groups"]:
        assert group["count"] <= ev.FINDINGS_PER_GROUP


def test_groups_carry_no_embedded_findings() -> None:
    """One source of truth on the wire.

    Groups used to embed their findings, which shipped every finding twice —
    44% of the payload, measured — and, once serialized, left the flat list and
    the grouped view as two independent copies that nothing keeps in step.
    Consumers join on finding["group"] instead.
    """
    import json

    from shared.utils.evidence import build_evidence_ledger

    ledger = build_evidence_ledger(_synthetic_rows(), now=NOW)
    groups = ledger["finding_groups"]
    assert groups
    for group in groups:
        assert set(group) == {"id", "label", "blurb", "count", "lead"}
    # The join every consumer performs must reproduce each group's count.
    for group in groups:
        joined = [f for f in ledger["findings"] if f["group"] == group["id"]]
        assert len(joined) == group["count"]
    # And no finding text is serialized twice.
    blob = json.dumps(groups)
    assert ledger["findings"][0]["takeaway"] not in blob


def test_findings_grouped_on_the_endpoint(tmp_path, monkeypatch) -> None:
    """Both views ship, and the thin fixture states nothing in either."""
    with _client(tmp_path, monkeypatch) as client:
        data = client.get("/evidence/ledger").json()
        assert data["findings"] == []
        assert data["finding_groups"] == []
