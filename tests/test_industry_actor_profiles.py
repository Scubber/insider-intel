"""Contracts for scripts/industry_actor_profiles.py — per-industry actor profiles.

Load-bearing tests: the report is labels and counts ONLY (roles, never
individuals — seeded free-text tokens must not survive into any output), the
basis funnel is v3-gated (pre-v3 rows were never asked their industry and
must not pollute the "unknown" pool), and the reading rules the reader must
carry are printed verbatim.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from shared.utils.evidence import DEFAULTED_EMPLOYMENT_STATE, SMALL_N_FLOOR

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "industry_actor_profiles.py"
_spec = importlib.util.spec_from_file_location("industry_actor_profiles", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

FS = "financial-services"


def _fx(
    role: str,
    industry: str | None = FS,
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
                "action": "copied files",
                "claim_status": "adjudicated",
                "evidence_quote": "SEEDQUOTE the defendant emailed the spreadsheet",
                "observables": [],
            }
            for _ in range(methods)
        ],
    }
    if industry is not None:
        f["industry"] = industry
    return f


def _row(n: int, forensics: dict | None, **extra) -> dict:
    row = {
        "link": f"https://ex.com/SEEDLINK-{n}",
        "title": f"SEEDTITLE case {n}",
        "published": f"2026-01-{(n % 28) + 1:02d}",
        "ai_summary": "SEEDSUMMARY analyst note",
    }
    if forensics is not None:
        row["forensics"] = forensics
    row.update(extra)
    return row


def test_script_is_stdlib_only() -> None:
    """The bare Actions runner has no pydantic and no shared package."""
    import re as _re

    src = _SCRIPT.read_text(encoding="utf-8")
    imports = _re.findall(r"^(?:from|import)\s+(\S+)", src, _re.M)
    allowed = {"__future__", "argparse", "importlib.util", "json", "pathlib", "sys", "collections"}
    assert set(imports) <= allowed, f"non-stdlib import crept in: {set(imports) - allowed}"
    assert callable(mod.funnel)


def test_funnel_is_monotone_and_v3_gated() -> None:
    rows = [
        _row(1, _fx("teller")),
        _row(1, _fx("loan officer")),  # duplicate link — last line wins
        _row(2, _fx("clerk", None, schema=2)),  # pre-v3: never asked its industry
        _row(3, _fx("trader", "unknown")),
        _row(4, _fx("trader", verdict=False)),
        _row(5, _fx("trader", methods=0)),
        _row(6, None),
    ]
    fn = mod.funnel(rows)
    assert fn["lines"] == 7
    assert fn["deduped_links"] == 6
    assert fn["with_forensics"] == 5
    assert fn["v3_tier"] == 4
    assert fn["verdict_true"] == 3
    assert fn["method_bearing"] == 2
    assert fn["cases_after_story_merge"] == 2
    stages = [
        fn["lines"],
        fn["deduped_links"],
        fn["with_forensics"],
        fn["v3_tier"],
        fn["verdict_true"],
        fn["method_bearing"],
        fn["cases_after_story_merge"],
    ]
    assert stages == sorted(stages, reverse=True)
    assert fn["not_asked_pre_v3"] == 1
    assert fn["industry_counts_v3"]["unknown"] == 1, "the pre-v3 row must NOT land here"
    assert fn["industry_counts_v3"][FS] == 2
    assert set(fn["industry_counts_v3"]) == set(mod.INDUSTRY_LABELS)


def test_story_key_merge_counts_one_case() -> None:
    rows = [
        _row(1, _fx("trader", posture="indictment"), story_key="s1"),
        _row(2, _fx("trader", posture="plea"), story_key="s1"),
    ]
    report = mod.build_report(rows, industry=FS)
    table = report["industry_table"]
    assert table["cases"] == 1
    assert table["rows"] == 2
    assert table["profiles"][0]["cases"] == 1
    assert table["profiles"][0]["rows"] == 2
    # The plea (stronger posture) is the representative, so the case is adjudicated.
    assert table["profiles"][0]["adjudicated_admitted"] == 1
    assert report["funnel"]["cases_after_story_merge"] == 1


def test_share_is_null_below_floor() -> None:
    rows = [_row(n, _fx("teller")) for n in range(SMALL_N_FLOOR - 1)]
    report = mod.build_report(rows, industry=FS)
    assert report["industry_table"]["profiles"][0]["share_pct"] is None
    assert "n/a" in mod.render(report)
    rows = [_row(n, _fx("teller")) for n in range(SMALL_N_FLOOR)]
    report = mod.build_report(rows, industry=FS)
    assert report["industry_table"]["profiles"][0]["share_pct"] == 100


def test_unknown_pool_table_present() -> None:
    rows = [_row(1, _fx("teller")), _row(2, _fx("contractor", "unknown"))]
    report = mod.build_report(rows, industry=FS)
    assert report["unknown_pool_table"]["cases"] == 1
    assert report["unknown_pool_table"]["profiles"][0]["function"] == "contractor/vendor"
    text = mod.render(report)
    assert "## Unknown pool" in text
    # And it never leaks into the requested industry's table.
    assert report["industry_table"]["cases"] == 1


def test_reading_rules_pinned() -> None:
    text = mod.render(mod.build_report([_row(1, _fx("teller"))], industry=FS))
    for phrase in (
        "victim organization's sector, not the actor's employer",
        '"current" is a default fill',
        "unknown pool",
        "collection lexicon",
        "roles, never individuals",
        f"percentages are suppressed below {SMALL_N_FLOOR}",
    ):
        assert phrase.lower() in text.lower(), phrase


def test_no_pii_survives() -> None:
    rows = [
        _row(1, _fx("SEEDROLE teller")),
        _row(2, _fx("SEEDROLE contractor", "unknown")),
        _row(3, _fx("SEEDROLE clerk", None, schema=2)),
    ]
    report = mod.build_report(rows, industry=FS)
    blob = mod.render(report) + json.dumps(mod.to_json(report))
    for token in (
        "SEEDROLE",
        "SEEDPROFILE",
        "Seedname",
        "SEEDTITLE",
        "SEEDLINK",
        "SEEDQUOTE",
        "SEEDSUMMARY",
    ):
        assert token not in blob, token
    # The words "title"/"link" legitimately appear in the reading-rules prose
    # and in the funnel's "deduped_links"; the contract is that no FIELD by
    # these names is carried, so walk the JSON structure for keys.
    banned = {"actor_role", "actor_profile", "title", "link", "evidence_quote", "ai_summary"}

    def keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from keys(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from keys(v)

    assert not banned & set(keys(mod.to_json(report)))


def test_default_fill_state_is_marked() -> None:
    rows = [_row(1, _fx("loan officer")), _row(2, _fx("former loan officer"))]
    report = mod.build_report(rows, industry=FS)
    by_state = {p["employment_state"]: p for p in report["industry_table"]["profiles"]}
    assert by_state[DEFAULTED_EMPLOYMENT_STATE]["note"] == "(default fill)"
    assert "note" not in by_state["former/fired"]
    assert "(default fill)" in mod.render(report)


def test_profiles_use_repaired_labels() -> None:
    rows = [_row(1, _fx("loan officer")), _row(2, _fx("contractor"))]
    report = mod.build_report(rows, industry=FS)
    functions = {p["function"] for p in report["industry_table"]["profiles"]}
    assert functions == {"finance/accounting/ops", "contractor/vendor"}


def test_motives_and_postures_per_profile() -> None:
    rows = [
        _row(1, _fx("teller", techs=("MT005", "MT005", "IF016"), posture="plea")),
        _row(2, _fx("teller", techs=("MT012",), posture="complaint")),
    ]
    report = mod.build_report(rows, industry=FS)
    key = "finance/accounting/ops · current"
    assert report["industry_table"]["motives"][key] == {"MT005": 1, "MT012": 1}
    assert report["industry_table"]["postures"][key] == {"plea": 1, "complaint": 1}


def test_cli_rejects_unknown_industry_and_writes_json(tmp_path, capsys) -> None:
    corpus = tmp_path / "c.jsonl"
    corpus.write_text(json.dumps(_row(1, _fx("teller"))) + "\n", encoding="utf-8")
    assert mod.main([str(corpus), "--industry", "SEEDNOTANINDUSTRY"]) == 2
    out_json = tmp_path / "r.json"
    assert mod.main([str(corpus), "--industry", FS, "--json", str(out_json)]) == 0
    assert "Actor profiles" in capsys.readouterr().out
    assert json.loads(out_json.read_text(encoding="utf-8"))["industry"] == FS
