"""GET /evidence/ledger — corpus-wide evidence aggregation for the sidebar."""

from __future__ import annotations

import re
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
    # 2026-09-04 repair: word-bounded patterns, first-match order is the contract.
    cases = {
        "police officer": ("unknown", "unknown"),
        "executive assistant": ("unknown", "unknown"),
        "administrative assistant": ("unknown", "unknown"),
        "Chief Compliance Officer": ("executive/officer", "current"),
        "compliance director": ("finance/accounting/ops", "current"),
        "managing director": ("executive/officer", "current"),
        "director of engineering": ("executive/officer", "current"),
        "account manager": ("front-office/sales", "current"),
        "IT contractor for the bank": ("contractor/vendor", "third-party"),
        "former trader": ("front-office/sales", "former/fired"),
        "credit union employee": ("unknown", "unknown"),
        "bank teller": ("finance/accounting/ops", "current"),
        "onboarding specialist": ("unknown", "unknown"),
        "directory services admin": ("technical", "current"),
        "IT administrator": ("technical", "current"),
        # Accepted: "analyst" is a technical token; finance-flavoured analysts
        # land here rather than in finance/accounting/ops.
        "financial analyst": ("technical", "current"),
    }
    for text, expected in cases.items():
        assert normalize_role(text) == expected, text


def test_contractor_outranks_executive_tokens() -> None:
    """The unbounded `cto` used to swallow "contractor" into executive/officer."""
    from shared.utils.evidence import normalize_role

    assert normalize_role("contractor") == ("contractor/vendor", "third-party")
    assert normalize_role("IT contractor") == ("contractor/vendor", "third-party")


def test_function_labels_are_the_documented_set() -> None:
    from shared.utils.evidence import _ROLE_FUNCTIONS

    assert {label for _, label in _ROLE_FUNCTIONS} == {
        "contractor/vendor",
        "temp/intern",
        "executive/officer",
        "manager",
        "technical",
        "front-office/sales",
        "finance/accounting/ops",
    }


def test_officer_titles_are_not_executives() -> None:
    """Bare `officer` is not an executive signal; only chief/C-suite titles are."""
    from shared.utils.evidence import normalize_role

    for text in ("loan officer", "compliance officer", "trust officer"):
        assert normalize_role(text)[0] == "finance/accounting/ops", text
    assert normalize_role("police officer") == ("unknown", "unknown")


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
    """A corpus rich enough to fire the behaviour rules.

    Deliberately varied: several roles across BOTH role axes, an explicit
    (non-defaulted) employment state, and an outside-telemetry record class
    alongside company-held ones. A single-role, single-artifact fixture cannot
    exercise any rule that compares one group against another.
    """
    roles = [
        ("chief financial officer", "corporate email"),
        ("chief executive officer", "corporate email"),
        ("chief executive officer", "brokerage trade records"),
        ("software engineer", "file access logs"),
        ("former employee engineer", "removable media"),
        ("third-party vendor consultant", "brokerage trade records"),
    ]
    rows = []
    for n in range(count):
        proven = n % 4 == 0
        role, artifact = roles[n % len(roles)]
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
                    "actor_role": role,
                    "candidate_technique_ids": ["IF016"],
                    "methods": [
                        {
                            "action": "wired funds to a personal account",
                            "claim_status": "adjudicated" if proven else "alleged",
                            "observables": [
                                {
                                    "description": "payment record",
                                    "artifact": artifact,
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

    ledger = build_evidence_ledger(_synthetic_rows(200), now=NOW)
    findings = {f["id"]: f for f in ledger["findings"]}
    assert "role-skew" in findings, "a varied corpus must state who these cases name"
    gap = findings["role-skew"]
    # Ranks are stamped in order and every card carries its own basis.
    assert [f["rank"] for f in ledger["findings"]] == list(range(1, len(ledger["findings"]) + 1))
    assert gap["basis"]["floor"] == 10
    assert gap["basis"]["n"] == ledger["enriched_cases"]
    assert gap["recommendations"] and gap["method"]


def test_findings_never_conflate_proven_and_alleged() -> None:
    """Any card quoting a proven share computes it from adjudicated_admitted
    alone — never from a total that folds allegations in."""
    from shared.utils.evidence import build_evidence_ledger

    ledger = build_evidence_ledger(_synthetic_rows(200), now=NOW)
    proven = ledger["strength_totals"]["adjudicated_admitted"]
    for fid in ("proven-over-index", "outside-telemetry"):
        card = next((f for f in ledger["findings"] if f["id"] == fid), None)
        if card is None:
            continue
        assert card["basis"]["n"] == proven, f"{fid} must rest on the proven count"
        assert str(proven) in card["takeaway"]


def test_findings_are_deterministic() -> None:
    """Same rows in, byte-identical cards out — no randomness, no clock."""
    from shared.utils.evidence import build_evidence_ledger

    rows = _synthetic_rows(200)
    assert (
        build_evidence_ledger(rows, now=NOW)["findings"]
        == (build_evidence_ledger(rows, now=NOW)["findings"])
    )


def test_findings_voice_bar() -> None:
    """The automatable half of the house voice rule (CLAUDE.md)."""
    from shared.utils.evidence import build_evidence_ledger

    ledger = build_evidence_ledger(_synthetic_rows(200), now=NOW)
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
    core = build_evidence_ledger(_synthetic_rows(200), now=NOW)
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
    ledger = module.build_evidence_ledger(_synthetic_rows(200), now=NOW)
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

    ledger = cli.build_evidence_ledger(_synthetic_rows(200), top=25)
    report = cli.render_markdown(ledger)
    assert "## Findings" in report
    assert "WHO DID IT" in report, "the CLI groups findings the way the page does"
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
    ledger = build_evidence_ledger(_synthetic_rows(200), now=NOW)
    assert ledger["findings"], "need findings to check the grouping of"
    for finding in ledger["findings"]:
        assert finding["group"] in known, f"{finding['id']} names unknown group"


def test_group_one_states_a_finding_not_a_caveat() -> None:
    """Group one is the group the page opens on load, so it must carry a claim
    about the cases. Two cards were cut in 2026-08 for describing the corpus
    instead — "most cases are still allegations" and "confident language is not
    a finding of fact". Both were already said by the stat strip and the legend;
    a card restating page furniture is filler. (The page also carried a
    LIMITATIONS wall then; that was removed 2026-08-26 — the rule stands
    without it, and the caveats that survive live in FINDINGS_CAVEAT.)
    """
    from shared.utils.evidence import FINDING_GROUPS

    assert FINDING_GROUPS[0][0] == "who"


def test_groups_render_in_taxonomy_order_and_carry_a_collapsed_lead() -> None:
    from shared.utils.evidence import FINDING_GROUPS, build_evidence_ledger

    ledger = build_evidence_ledger(_synthetic_rows(200), now=NOW)
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

    ledger = build_evidence_ledger(_synthetic_rows(200), now=NOW)
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
    ledger = ev.build_evidence_ledger(_synthetic_rows(200), now=NOW)
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

    ledger = build_evidence_ledger(_synthetic_rows(200), now=NOW)
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


def test_memo_furniture_survives_the_service_layer(tmp_path, monkeypatch) -> None:
    """bottom_line and findings_caveat are keys the API must not drop.

    The service pops five technique_* maps before serving, so anything the page
    reads has to be checked on the ENDPOINT, not on the core payload. Both keys
    ship here as null because the fixture is below the floor — present and
    null, never absent.
    """
    with _client(tmp_path, monkeypatch) as client:
        data = client.get("/evidence/ledger").json()
        assert "bottom_line" in data
        assert "findings_caveat" in data
        assert data["bottom_line"] is None
        assert data["findings_caveat"] is None


# ---------------------------------------------------------------------------
# Rules state findings, not caveats
# ---------------------------------------------------------------------------


def test_no_rule_reports_the_defaulted_employment_state() -> None:
    """normalize_role fills employment_state with "current" whenever a function
    matched and no boundary language appeared. A card headlining that bucket
    would be reporting the fill value, not a measurement — and both role rules
    read that axis.
    """
    from shared.utils.evidence import DEFAULTED_EMPLOYMENT_STATE, build_evidence_ledger

    # Every row here carries a role but NO boundary language, so the whole
    # corpus lands in the defaulted bucket.
    rows = _synthetic_rows(200)
    for row in rows:
        row["forensics"]["actor_role"] = "chief executive officer"
    ledger = build_evidence_ledger(rows, now=NOW)
    state_rows = {r["label"] for r in ledger["roles"]["employment_state"]}
    assert DEFAULTED_EMPLOYMENT_STATE in state_rows, "fixture should hit the default"
    for finding in ledger["findings"]:
        label = (finding.get("evidence") or {}).get("label")
        kind = (finding.get("evidence") or {}).get("kind")
        assert not (kind == "role_employment_state" and label == DEFAULTED_EMPLOYMENT_STATE)


def test_outside_telemetry_names_a_record_no_sensor_produces() -> None:
    from shared.utils.evidence import OUTSIDE_TELEMETRY_HOLDERS, build_evidence_ledger

    ledger = build_evidence_ledger(_synthetic_rows(200), now=NOW)
    card = next((f for f in ledger["findings"] if f["id"] == "outside-telemetry"), None)
    assert card is not None, "the fixture carries brokerage records; the rule should fire"
    family = card["evidence"]["label"]
    assert family in OUTSIDE_TELEMETRY_HOLDERS
    # Names the actual holder rather than reciting every possibility.
    assert OUTSIDE_TELEMETRY_HOLDERS[family] in card["takeaway"]
    # The collection-bias caveat is load-bearing: these lanes are seeded by name.
    assert "over-represented" in card["method"]


def test_outside_telemetry_list_is_authored_not_inferred() -> None:
    """An earlier draft inferred this set from the DT crosswalk and swept in
    account-opening records (a bank generates those daily) and public-vs-internal
    statements (whose internal half is company-held). Both must stay out."""
    from shared.utils.evidence import OUTSIDE_TELEMETRY_FAMILIES

    assert "entity-formation / account-opening records" not in OUTSIDE_TELEMETRY_FAMILIES
    assert "public statements vs internal records" not in OUTSIDE_TELEMETRY_FAMILIES
    # And every member must be a real artifact family, not a typo.
    from shared.utils.evidence import _ARTIFACT_FAMILIES

    known = {label for _, label in _ARTIFACT_FAMILIES}
    assert OUTSIDE_TELEMETRY_FAMILIES <= known


def test_no_rule_describes_the_corpus_instead_of_the_cases() -> None:
    """The drift guard for the whole rule set.

    Two cards were cut in 2026-08 for describing our data rather than the
    cases — the page's stat strip and legend already carried both, as did the
    LIMITATIONS wall since removed. A new rule whose headline is about
    counting, confidence, or how to read the page is the same mistake.
    """
    from shared.utils.evidence import build_evidence_ledger

    meta_tells = (
        "still allegations",
        "not verdicts",
        "finding of fact",
        "this page",
        "these numbers",
        "the corpus",
        "read this",
    )
    ledger = build_evidence_ledger(_synthetic_rows(200), now=NOW)
    assert ledger["findings"]
    for finding in ledger["findings"]:
        headline = f"{finding['title']} {finding['stat_label']}".lower()
        for tell in meta_tells:
            assert tell not in headline, f"{finding['id']} headlines a caveat: {tell}"


# ---------------------------------------------------------------------------
# Anti-slop guards (operator review, 2026-08-25)
#
# The findings section was correct and still read like generated filler: five
# identically-shaped cards, headlines that restated their own group header,
# rhetorical scaffolding standing in for claims, advice that named nothing,
# and the same three caveats repeated on every card. Each tell below is now
# mechanically unrepeatable, because prose regressions are invisible to a test
# that only checks arithmetic.
# ---------------------------------------------------------------------------

# Scaffolding a findings memo never uses. Each of these shipped once.
_RHETORICAL_TELLS = (
    "the question is not",
    "it is whether",
    "is the finding",
    "what this means is",
    "at the end of the day",
    "the reality is",
)

_ITM_ID = re.compile(r"\b[A-Z]{2}\d{3}(?:\.\d{3})?\b")


def _finding_strings(finding: dict) -> list[str]:
    return [
        finding["title"],
        finding["takeaway"],
        finding.get("method") or "",
        *(finding.get("recommendations") or []),
    ]


def _two_year_rows() -> list[dict]:
    """A corpus that grows year over year, so rising-technique fires.

    _synthetic_rows is single-year by design; the technique rules need two
    complete years, both clear of the floor, with the later one larger.
    """
    rows = _synthetic_rows(60, year="2022") + _synthetic_rows(120, year="2023")
    for i, row in enumerate(rows[60:]):
        row["link"] += f"-y2{i}"
    return rows


def test_no_finding_uses_rhetorical_scaffolding() -> None:
    """State the finding; never announce that you are stating one."""
    from shared.utils.evidence import build_evidence_ledger

    ledger = build_evidence_ledger(_synthetic_rows(200), now=NOW)
    assert ledger["findings"]
    for finding in ledger["findings"]:
        blob = " ".join(_finding_strings(finding)).lower()
        for tell in _RHETORICAL_TELLS:
            assert tell not in blob, f"{finding['id']} uses rhetorical filler: {tell}"


def test_headline_carries_its_own_subject() -> None:
    """A title must still read true with its group header deleted.

    The tell this kills is "One technique climbed faster than the rest" — a
    headline with no subject, whose subject was buried in the body as a bare
    ITM id.
    """
    from shared.utils.evidence import build_evidence_ledger

    a = build_evidence_ledger(_synthetic_rows(200), now=NOW)
    b = build_evidence_ledger(_synthetic_rows(40), now=NOW)
    titles_a = {f["id"]: f["title"] for f in a["findings"]}
    titles_b = {f["id"]: f["title"] for f in b["findings"]}
    shared_ids = set(titles_a) & set(titles_b)
    assert shared_ids
    # Every title carries at least one value read off its own ledger, so two
    # corpora that differ cannot produce byte-identical headlines.
    assert any(titles_a[i] != titles_b[i] for i in shared_ids), (
        "no headline varies with the data — the titles are static strings"
    )


def test_headline_does_not_restate_its_group_header() -> None:
    from shared.utils.evidence import FINDING_GROUPS, build_evidence_ledger

    labels = {gid: (label, blurb) for gid, label, blurb in FINDING_GROUPS}
    ledger = build_evidence_ledger(_synthetic_rows(200), now=NOW)
    assert ledger["findings"]
    for finding in ledger["findings"]:
        label, blurb = labels[finding["group"]]
        title = finding["title"].casefold()
        assert label.casefold() not in title
        assert blurb.casefold() not in title


def test_shared_caveats_render_once_not_per_card() -> None:
    """The three caveats true of every card live in FINDINGS_CAVEAT.

    Repeating them per finding is what teaches a reader to skip exactly the
    warnings that matter.
    """
    from shared.utils.evidence import FINDINGS_CAVEAT, build_evidence_ledger

    ledger = build_evidence_ledger(_synthetic_rows(200), now=NOW)
    assert ledger["findings_caveat"] == FINDINGS_CAVEAT
    methods = [f["method"] for f in ledger["findings"] if f.get("method")]
    assert len(methods) == len(set(methods)), "two findings ship the same caveat"
    for sentence in ("read out of filings by a model", "filtered sample", "how deep we have swept"):
        for method in methods:
            assert sentence not in method, f"a card repeats the shared caveat: {sentence}"


def test_recommendations_name_their_own_finding() -> None:
    """Advice that could sit under any card is padding, not advice."""
    from shared.utils.evidence import attach_catalog_titles, build_evidence_ledger

    # The two-year fixture so the technique rule is covered too — its advice
    # names the technique through the slot, and must survive the join naming it.
    ledger = build_evidence_ledger(_two_year_rows(), now=NOW)
    assert ledger["findings"]
    attach_catalog_titles(
        ledger,
        {
            str(f["evidence"]["label"]).upper(): "Exfiltration over personal email"
            for f in ledger["findings"]
            if (f.get("evidence") or {}).get("kind") == "technique"
        },
    )
    for finding in ledger["findings"]:
        recs = finding.get("recommendations") or []
        assert recs, f"{finding['id']} gives no advice"
        assert len(recs) <= 2, f"{finding['id']} ships {len(recs)} bullets — the cap is 2"
        ref = finding.get("evidence") or {}
        subject = str(ref.get("title") or ref.get("label") or "")
        assert subject
        assert subject in recs[0], f"{finding['id']}'s first bullet does not name {subject!r}"


def test_takeaway_is_at_most_two_sentences() -> None:
    from shared.utils.evidence import build_evidence_ledger

    ledger = build_evidence_ledger(_synthetic_rows(200), now=NOW)
    assert ledger["findings"]
    for finding in ledger["findings"]:
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", finding["takeaway"].strip()) if s]
        assert len(sentences) <= 2, f"{finding['id']} runs to {len(sentences)} sentences"


def test_findings_carry_a_lead_and_supporting_weight() -> None:
    """One card per group leads; the rest support.

    Five cards at identical visual weight read as generated filler however good
    each one is, so the weight is part of the payload, not a client whim.
    """
    from shared.utils.evidence import build_evidence_ledger

    ledger = build_evidence_ledger(_synthetic_rows(200), now=NOW)
    findings = ledger["findings"]
    assert findings
    assert {f["weight"] for f in findings} <= {"lead", "supporting"}
    by_group: dict[str, list[dict]] = {}
    for f in findings:
        by_group.setdefault(f["group"], []).append(f)
    for members in by_group.values():
        assert members[0]["weight"] == "lead"
        assert all(m["weight"] == "supporting" for m in members[1:])


def test_bottom_line_states_no_claim_the_findings_do_not() -> None:
    """A precis, never a sixth rule."""
    from shared.utils.evidence import build_evidence_ledger

    ledger = build_evidence_ledger(_synthetic_rows(200), now=NOW)
    line = ledger["bottom_line"]
    assert line
    known = {str(ledger["enriched_cases"]), str(ledger["strength_totals"]["adjudicated_admitted"])}
    for f in ledger["findings"]:
        known.update(re.findall(r"\d+", f["title"]))
        known.update(str(v) for v in (f.get("basis") or {}).values() if isinstance(v, int))
    for number in re.findall(r"\d+", line):
        assert number in known, f"the bottom line introduces {number}, which no finding states"


def test_bottom_line_is_silent_below_the_floor() -> None:
    from shared.utils.evidence import build_evidence_ledger

    ledger = build_evidence_ledger(_synthetic_rows(2), now=NOW)
    assert ledger["findings"] == []
    assert ledger["bottom_line"] is None
    assert ledger["findings_caveat"] is None


def test_a_technique_is_never_a_bare_itm_id_once_the_catalog_has_joined() -> None:
    """Operator rule: spell the technique out, and make the code a link.

    The core cannot do this itself — it has no catalog by contract — so it
    writes TECHNIQUE_SLOT and attach_catalog_titles fills it at the one seam
    that does.
    """
    from shared.utils.evidence import attach_catalog_titles, build_evidence_ledger

    ledger = build_evidence_ledger(_two_year_rows(), now=NOW)
    tech = [f for f in ledger["findings"] if (f.get("evidence") or {}).get("kind") == "technique"]
    assert tech, "fixture no longer fires a technique finding — this guard is asleep"
    titles = {str(f["evidence"]["label"]).upper(): "Exfiltration over personal email" for f in tech}
    attach_catalog_titles(ledger, titles)
    for finding in tech:
        for text in _finding_strings(finding):
            assert not _ITM_ID.search(text), f"{finding['id']} prints a bare ITM id: {text!r}"
            assert "Exfiltration over personal email" in text or "{technique}" not in text


def test_no_surface_ever_ships_an_unfilled_technique_slot() -> None:
    """Joined, catalog-miss and CLI paths must each degrade to real words."""
    from shared.utils.evidence import (
        TECHNIQUE_SLOT,
        attach_catalog_titles,
        build_evidence_ledger,
        fill_technique_slot,
    )

    # Catalog MISS: no title for the id. Must fall back to the id, never to a
    # hole and never to the raw slot.
    miss = build_evidence_ledger(_two_year_rows(), now=NOW)
    attach_catalog_titles(miss, {})
    # CLI path: fills with the bare id directly, which is correct for a text
    # report with nowhere to link.
    cli = build_evidence_ledger(_two_year_rows(), now=NOW)
    for finding in cli["findings"]:
        ref = finding.get("evidence") or {}
        if ref.get("kind") == "technique":
            fill_technique_slot(finding, str(ref["label"]))
    for ledger in (miss, cli):
        for finding in ledger["findings"]:
            for text in _finding_strings(finding):
                assert TECHNIQUE_SLOT not in text, f"{finding['id']} leaks the raw slot"


def test_cli_report_never_prints_the_technique_slot() -> None:
    """The CLI has no catalog, so it fills the slot with the bare id itself.

    Loaded the way the CLI loads its own core — as a bare file — because that
    load is the file's hardest constraint.
    """
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "evidence_ledger.py"
    spec = importlib.util.spec_from_file_location("evidence_ledger_slot", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    ledger = cli.build_evidence_ledger(_two_year_rows(), top=25)
    for finding in ledger["findings"]:
        ref = finding.get("evidence") or {}
        if ref.get("kind") == "technique":
            cli.fill_technique_slot(finding, str(ref["label"]))
            if finding is ledger["findings"][0] and ledger.get("bottom_line"):
                ledger["bottom_line"] = ledger["bottom_line"].replace(
                    "{technique}", str(ref["label"])
                )
    text = cli.render_markdown(ledger)
    assert "{technique}" not in text
    assert "**Bottom line.**" in text
    assert "#### F1 — " in text
    # The shared caveat prints once for the section, not once per finding.
    assert text.count("read out of filings by a model") == 1


def test_the_shipped_catalog_names_the_technique_a_finding_can_cite() -> None:
    """The original complaint, as a test.

    A finding read "Cases citing MT003.002 went from 34 to 55" — a bare ITM id
    where a name belongs. The slot fixes the plumbing, but only if the SHIPPED
    catalog actually carries a title for the id: a join against an empty or
    stale catalog degrades to the id and looks identical.

    So: resolve against `shared/data/itm_index.json` itself, and assert the
    resulting title is a real name rather than the id echoed back.
    """
    import json
    from pathlib import Path

    from shared.utils.evidence import attach_catalog_titles, build_evidence_ledger

    catalog = json.loads(
        (Path(__file__).resolve().parent.parent / "shared" / "data" / "itm_index.json").read_text(
            encoding="utf-8"
        )
    )
    titles = {str(t["id"]).upper(): t["title"] for t in catalog["techniques"] if t.get("title")}

    ledger = build_evidence_ledger(_two_year_rows(), now=NOW)
    tech = [f for f in ledger["findings"] if (f.get("evidence") or {}).get("kind") == "technique"]
    assert tech, "fixture no longer fires a technique finding — this guard is asleep"
    attach_catalog_titles(ledger, titles)
    for finding in tech:
        ref = finding["evidence"]
        assert ref["title_missing"] is False, f"{ref['label']} is not in the shipped ITM catalog"
        assert ref["title"] != ref["label"], f"{ref['label']} resolved to its own id"
        # And the name, not the id, is what the reader sees.
        assert ref["title"] in finding["title"]
        assert not _ITM_ID.search(finding["title"])


def test_a_catalog_miss_is_stamped_not_swallowed() -> None:
    """Degrading to the id is acceptable; degrading SILENTLY is not.

    Without the stamp, a payload carrying `title == "MT003.002"` is
    indistinguishable from a technique whose catalog title genuinely is its id,
    so the client cannot mark it and no test can catch it.
    """
    from shared.utils.evidence import attach_catalog_titles, build_evidence_ledger

    ledger = build_evidence_ledger(_two_year_rows(), now=NOW)
    attach_catalog_titles(ledger, {})
    tech = [f for f in ledger["findings"] if (f.get("evidence") or {}).get("kind") == "technique"]
    assert tech
    for finding in tech:
        ref = finding["evidence"]
        assert ref["title_missing"] is True
        assert ref["title"] == ref["label"]
