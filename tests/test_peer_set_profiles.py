"""Contracts for scripts/peer_set_profiles.py — the private peer-set study lane.

Load-bearing tests: the authored peer set carries only SAFE aliases (never a
bare common English word; single tokens only from the allowlist), matching
is word-bounded ("Voyager" never credits Voya, "principal amount" never
credits Principal) and never reads enrichment_history, per-firm counts are
mention counts, the pooled profile table uses the repaired labels, the Voya
appendix carries the watchlist caveat, and the report is labels and counts
ONLY — seeded free-text tokens must not survive.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from shared.utils.evidence import SMALL_N_FLOOR

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "peer_set_profiles.py"
_spec = importlib.util.spec_from_file_location("peer_set_profiles", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

SET_ID = "retirement-insurance-asset-mgmt"
CATALOG = mod.load_peer_sets()
FIRMS = CATALOG["peer_sets"][SET_ID]["firms"]
BARE_BANNED = {"fidelity", "principal", "lincoln", "nationwide", "equitable", "empower", "vanguard"}


def _fx(
    role: str,
    industry: str | None = "financial-services",
    *,
    schema: int = 3,
    verdict: bool = True,
    posture: str = "conviction",
    techs=("MT005", "IF016"),
    methods: int = 1,
) -> dict:
    f = {
        "schema_version": schema,
        "is_insider_case": verdict,
        "actor_role": role,
        "actor_profile": f"{role} — SEEDPROFILE Jane Q. Seedname",
        "legal_posture": posture,
        "candidate_technique_ids": list(techs),
        "methods": [
            {
                "action": "copied files SEEDACTION",
                "claim_status": "adjudicated",
                "evidence_quote": "SEEDQUOTE the defendant emailed the spreadsheet",
                "observables": [{"artifact": "email logs SEEDARTIFACT", "channel": "email"}],
            }
            for _ in range(methods)
        ],
    }
    if industry is not None:
        f["industry"] = industry
    return f


def _row(n: int, text: str, forensics: dict | None, **extra) -> dict:
    row = {
        "link": f"https://ex.com/SEEDLINK-{n}",
        "title": f"SEEDTITLE case {n}",
        "published": f"2026-01-{(n % 28) + 1:02d}",
        "clean_text": text,
        "summary": "SEEDSUMMARY short",
        "ai_summary": "SEEDNOTE analyst note",
    }
    if forensics is not None:
        row["forensics"] = forensics
    row.update(extra)
    return row


def _report(rows, **kw):
    return mod.build_report(rows, peer_set=SET_ID, **kw)


def test_script_is_stdlib_only() -> None:
    src = _SCRIPT.read_text(encoding="utf-8")
    imports = re.findall(r"^(?:from|import)\s+(\S+)", src, re.M)
    allowed = {
        "__future__",
        "argparse",
        "importlib.util",
        "json",
        "pathlib",
        "re",
        "sys",
        "collections",
    }
    assert set(imports) <= allowed, f"non-stdlib import crept in: {set(imports) - allowed}"
    assert "enrichment_history" not in mod.TEXT_FIELDS


def test_peer_set_alias_safety() -> None:
    single_ok = {a.lower() for a in CATALOG["single_token_ok"]}
    expected = {
        "voya",
        "fidelity",
        "vanguard",
        "empower",
        "tiaa",
        "principal",
        "prudential",
        "metlife",
        "lincoln",
        "nationwide",
        "john-hancock",
        "transamerica",
        "equitable",
    }
    assert set(FIRMS) == expected
    seen: dict[str, str] = {}
    for fid, firm in FIRMS.items():
        assert firm["display"]
        assert firm["aliases"], fid
        for alias in firm["aliases"]:
            low = " ".join(alias.lower().split())
            assert low not in BARE_BANNED, f"bare common word as alias: {alias!r}"
            assert len(alias.split()) >= 2 or alias in CATALOG["single_token_ok"], alias
            assert len(low) >= 4, alias
            assert seen.setdefault(low, fid) == fid, f"alias {alias!r} homed twice"
    for token in single_ok:
        assert token not in BARE_BANNED
    assert CATALOG["rules"] and CATALOG["notes"]
    notes = " ".join(CATALOG["notes"])
    assert "bare 'Fidelity'" in notes and "bond" in notes


def test_word_bounded_matching() -> None:
    pattern, homes = mod.compile_firm_matcher(FIRMS)
    named = lambda text: mod.firms_named({"clean_text": text}, pattern, homes)  # noqa: E731
    assert named("Voyager Digital collapsed") == set()
    assert named("the principal amount of the loan") == set()
    assert named("equitable relief and nationwide injunction, a Lincoln Navigator") == set()
    assert named("fidelity bond coverage; he owed a duty of fidelity") == set()
    assert named("worked at Voya Financial") == {"voya"}
    assert named("VOYA   RETIREMENT insurance") == {"voya"}
    assert named("Principal Financial Group, Inc.") == {"principal"}
    assert named("moved to TIAA-CREF in 2019") == {"tiaa"}
    assert named("PGIM and Manulife") == {"prudential", "john-hancock"}
    assert named("a Fidelity Investments account") == {"fidelity"}
    # Fields: title/summary/ai_summary count, enrichment_history never.
    assert mod.firms_named({"title": "MetLife v. Doe"}, pattern, homes) == {"metlife"}
    assert mod.firms_named({"ai_summary": "at Nationwide Mutual"}, pattern, homes) == {"nationwide"}
    hist = {"enrichment_history": [{"ai_summary": "Voya Financial"}], "clean_text": "nothing"}
    assert mod.firms_named(hist, pattern, homes) == set()


def test_per_firm_mention_counts_and_slice_gating() -> None:
    rows = [
        _row(1, "loan officer at Voya Financial", _fx("loan officer")),
        _row(2, "Voyager Digital exec", _fx("executive")),
        _row(3, "the principal amount of the loan", _fx("teller")),
        _row(4, "Principal Financial Group analyst", _fx("analyst")),
        _row(5, "TIAA engineer", _fx("engineer", "technology")),
        _row(6, "Voya Retirement clerk", _fx("clerk", schema=2)),  # pre-v3: out
        _row(7, "Voya Financial", _fx("clerk", verdict=False)),  # verdict false: out
        _row(8, "Voya and TIAA together", _fx("trader", posture="complaint")),
    ]
    report = _report(rows)
    assert report["funnel"]["peer_set_matched"] == 4
    by = {c["firm"]: c for c in report["firm_table"]}
    assert by["voya"]["cases"] == 2 and by["voya"]["financial_services"] == 2
    assert by["voya"]["adjudicated_admitted"] == 1  # row 8 is a complaint
    assert by["tiaa"]["cases"] == 2 and by["tiaa"]["financial_services"] == 1
    assert by["principal"]["cases"] == 1
    assert by["fidelity"]["cases"] == 0
    assert report["firms_per_case"] == {"1": 3, "2": 1}
    assert report["firm_table"][0]["firm"] in {"voya", "tiaa"}
    assert report["peer_table"]["cases"] == 4


def test_pooled_profile_table_uses_repaired_labels() -> None:
    rows = [
        _row(1, "Voya Financial", _fx("loan officer")),
        _row(2, "Empower Retirement", _fx("contractor")),
        _row(3, "Lincoln Financial", _fx("former loan officer")),
    ]
    report = _report(rows)
    profiles = report["peer_table"]["profiles"]
    assert {p["function"] for p in profiles} == {"finance/accounting/ops", "contractor/vendor"}
    by_state = {(p["function"], p["employment_state"]): p for p in profiles}
    assert by_state[("finance/accounting/ops", "current")]["note"] == "(default fill)"
    assert "note" not in by_state[("finance/accounting/ops", "former/fired")]
    assert report["peer_table"]["profiles"][0]["share_pct"] is None  # below floor
    assert "(default fill)" in mod.render(report)


def test_share_is_null_below_floor() -> None:
    rows = [_row(n, "Voya Financial", _fx("teller")) for n in range(SMALL_N_FLOOR)]
    assert _report(rows)["peer_table"]["profiles"][0]["share_pct"] == 100


def test_voya_appendix_with_watchlist_caveat() -> None:
    rows = [_row(1, "Voya Financial", _fx("teller")), _row(2, "MetLife", _fx("teller"))]
    report = _report(rows)
    app = report["voya_appendix"]
    assert app["table"]["cases"] == 1
    assert app["watchlist_default"] == "Voya, Voya India"
    text = mod.render(report)
    assert "## Appendix — Voya-only rows" in text
    assert "COURTLISTENER_COMPANY_WATCHLIST" in text
    assert "collection artifact" in text
    assert "by construction" in text.lower()


def test_ledger_section_is_counts_only() -> None:
    report = _report([_row(1, "Voya Financial", _fx("teller"))])
    ledger = report["ledger"]
    assert ledger["enriched_cases"] == 1
    assert ledger["techniques"][0]["id"] in {"MT005", "IF016"}
    assert ledger["detected_by"][0]["cases"] == 1
    assert "examples" not in ledger["detected_by"][0]
    assert "exemplars" not in ledger["techniques"][0]
    for banned in ("technique_hunts", "technique_terms", "technique_behaviors", "findings"):
        assert banned not in ledger


def test_reading_rules_pinned() -> None:
    text = mod.render(_report([_row(1, "Voya Financial", _fx("teller"))])).lower()
    for phrase in (
        "roles, never individuals",
        "presence in the record, not fault",
        "victim organization's sector, not the actor's employer",
        '"current" is a default fill',
        f"percentages are suppressed below {SMALL_N_FLOOR}",
        "collection lexicon",
        "enrichment history is never scanned",
        "private export",
    ):
        assert phrase in text, phrase


def test_no_pii_survives() -> None:
    rows = [
        _row(1, "Voya Financial SEEDBODY", _fx("SEEDROLE teller")),
        _row(2, "TIAA SEEDBODY", _fx("SEEDROLE contractor", "unknown")),
        _row(3, "Voya Financial SEEDBODY", _fx("SEEDROLE clerk", None, schema=2)),
    ]
    report = _report(rows)
    blob = mod.render(report) + json.dumps(mod.to_json(report))
    for token in (
        "SEEDROLE",
        "SEEDPROFILE",
        "Seedname",
        "SEEDTITLE",
        "SEEDLINK",
        "SEEDQUOTE",
        "SEEDSUMMARY",
        "SEEDNOTE",
        "SEEDBODY",
        "SEEDACTION",
        "SEEDARTIFACT",
    ):
        assert token not in blob, token
    banned = {
        "actor_role",
        "actor_profile",
        "title",
        "link",
        "evidence_quote",
        "ai_summary",
        "clean_text",
        "summary",
        "exemplars",
        "examples",
        "quote",
    }

    def keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from keys(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from keys(v)

    assert not banned & set(keys(mod.to_json(report)))


def test_cli_rejects_unknown_set_and_writes_json(tmp_path, capsys) -> None:
    corpus = tmp_path / "c.jsonl"
    corpus.write_text(json.dumps(_row(1, "Voya Financial", _fx("teller"))) + "\n", encoding="utf-8")
    assert mod.main([str(corpus), "--peer-set", "SEEDNOTASET"]) == 2
    out_json = tmp_path / "r.json"
    assert mod.main([str(corpus), "--peer-set", SET_ID, "--json", str(out_json)]) == 0
    assert "Peer-set study" in capsys.readouterr().out
    assert json.loads(out_json.read_text(encoding="utf-8"))["peer_set"] == SET_ID


def test_workflow_auth_block_matches_industry_lane() -> None:
    wf = _ROOT / ".github" / "workflows"
    peer = (wf / "corpus-peerset.yml").read_text(encoding="utf-8")
    ind = (wf / "corpus-industry.yml").read_text(encoding="utf-8")

    def auth(text: str) -> str:
        return text[text.index("      - uses: actions/checkout") : text.index("\n      - name:")]

    assert auth(peer) == auth(ind)
    assert "export/peer-set-profiles-$PEER_SET" in peer
    assert "upload-artifact" not in peer
