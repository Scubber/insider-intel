#!/usr/bin/env python3
"""Actor-profile table for one victim industry — roles, never individuals.

Read-only research report (dispatched by .github/workflows/corpus-industry.yml,
runnable locally): who the insiders WERE, by job function and employment
state, inside cases where the victim organization sat in one industry
(schema v3 ``forensics.industry``, financial-services first per operator
priority). Every number is a count of cases or rows; no name, title, link,
quote or summary ever enters the report — the tests seed unique tokens in
every free-text field and assert none survives.

Basis funnel (each stage is a subset of the one before it):
  lines → deduped links (last line wins, the API's own read) → with forensics
  → v3 tier → verdict-true → method-bearing → cases after story merge.
Industry is only counted on the v3 tier: earlier schemas never asked, so a
pre-v3 verdict-true row is reported as "not asked", NEVER folded into
"unknown" (which is the v3 enricher's own answer for a silent source).

Two framings (``--by``): ``victim`` (default) slices on the victim
organization's sector; ``employer`` slices on the insider's OWN employer's
sector (the additive v3 field ``forensics.actor_employer_sector``), whoever
the victim was. Both modes end with a VICTIM × EMPLOYER cross-tab: did the
insider work for the victim, for a firm in another sector, or is that not
on record. Victim-mode output is byte-identical to the pre-``--by`` report
up to that trailing section (test-pinned).

Stdlib only — the bare Actions runner has no pydantic. The aggregation core
(shared/utils/evidence.py) is loaded as a bare file, same as evidence_ledger.py.

Usage: industry_actor_profiles.py CORPUS.jsonl [--industry financial-services]
       [--by victim|employer] [--json out.json] [--top 15]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_CORE_PATH = Path(__file__).resolve().parent.parent / "shared" / "utils" / "evidence.py"
_spec = importlib.util.spec_from_file_location("evidence_core", _CORE_PATH)
_core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core)

SMALL_N_FLOOR = _core.SMALL_N_FLOOR
DEFAULTED_EMPLOYMENT_STATE = _core.DEFAULTED_EMPLOYMENT_STATE
INDUSTRY_LABELS = _core.INDUSTRY_LABELS
STRENGTH_RANK = _core.STRENGTH_RANK
V3_SCHEMA = 3
UNKNOWN_PROFILE = ("unknown", "unknown")
STRENGTH_KEYS = {
    "adjudicated/admitted": "adjudicated_admitted",
    "alleged": "alleged",
    "reported/unclear": "reported_unclear",
}

# Printed verbatim in every report — the reader must carry these, so the
# report carries them. tests/test_industry_actor_profiles.py pins the phrases.
READING_RULES = (
    "INDUSTRY is the victim organization's sector, not the actor's employer:"
    " a contractor who hit a bank is a financial-services case.",
    'EMPLOYMENT STATE "current" is a default fill: the normalizer stamps it whenever a'
    " job function matched and the text carried no boundary language (former, resigned,"
    " contractor). Rows marked (default fill) measure the absence of that language, not"
    " tenure.",
    "The UNKNOWN POOL table (industry = unknown at v3) is the contamination check: the"
    " enricher could not read a sector from the source. If its profile mix mirrors the"
    " requested industry, the industry table is undercounting; if it differs, it is not.",
    "Pre-v3 rows are NOT ASKED, not unknown: their schema had no industry field. They"
    " never enter the unknown pool.",
    "Insider trading and embezzlement are literal COLLECTION LEXICON queries (CourtListener"
    " DEFAULT_QUERIES), so their motive and posture counts reflect what the corpus went"
    " looking for, not the base rate.",
    "ROLES, NEVER INDIVIDUALS: every cell is a count of cases; no name, title, link or"
    " quote is in this report.",
    f"Percentages are suppressed below {SMALL_N_FLOOR} cases (share shows as n/a).",
    "Case strength is the strongest method claim, capped by the document's legal posture:"
    " a complaint can never mint an adjudicated case.",
)

# Employer mode swaps the two sector rules and adds the None caveat; every
# other rule is shared. Victim mode prints READING_RULES unchanged.
EMPLOYER_READING_RULES = (
    "INDUSTRY here is the INSIDER'S OWN EMPLOYER'S sector (v3 additive field"
    " actor_employer_sector), whoever the victim was: a bank employee who hit a hospital"
    " is a financial-services case in this report. The victim's sector is the VICTIM ×"
    " EMPLOYER table.",
    READING_RULES[1],
    "The UNKNOWN POOL table (employer unknown / not asked at v3) is the contamination"
    " check: the enricher could not read the insider's employer from the source, or was"
    " never asked. If its profile mix mirrors the requested sector, the sector table is"
    " undercounting; if it differs, it is not.",
    "A None employer sector after the field's prompt-contract date (ADDITIVE_FIELD_"
    "CONTRACT_SINCE in shared/schemas/forensics.py) means the model answered unknown;"
    " before it, the question was never asked. This script does not distinguish the two"
    " (stdlib, no stamp read): both land in the unknown pool.",
    *READING_RULES[3:],
)

MODES = ("victim", "employer")
RELATIONS = ("same", "other", "unknown")
RELATION_LABELS = {
    "victim": {
        "same": "same as victim",
        "other": "other sector",
        "unknown": "employer unknown / not asked",
    },
    "employer": {
        "same": "same as employer",
        "other": "other sector",
        "unknown": "victim unknown",
    },
}


def _forensics(row: dict) -> dict:
    f = row.get("forensics")
    return f if isinstance(f, dict) else {}


def _schema_version(f: dict) -> int:
    try:
        return int(f.get("schema_version") or 1)
    except (TypeError, ValueError):
        return 1


def _methods(f: dict) -> list[dict]:
    return [m for m in (f.get("methods") or []) if isinstance(m, dict)]


def _posture(f: dict) -> str:
    return str(f.get("legal_posture") or "unknown").strip().lower() or "unknown"


def _strength(f: dict) -> str:
    return _core.case_strength(_methods(f), _posture(f))


def _profile(f: dict) -> tuple[str, str]:
    # Identical inputs to build_evidence_ledger's role axis.
    return _core.normalize_role(str(f.get("actor_role") or ""), str(f.get("actor_profile") or ""))


def _is_v3_verdict_true(f: dict) -> bool:
    return bool(f) and _schema_version(f) >= V3_SCHEMA and f.get("is_insider_case") is True


def _story_groups(rows: list[dict]) -> list[list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for idx, row in enumerate(rows):
        key = str(row.get("story_key") or "").strip() or str(row.get("link") or f"row-{idx}")
        groups[key].append(row)
    return list(groups.values())


def _representative(group: list[dict]) -> dict:
    """Strongest posture, then most methods, then newest — one row per story."""

    def rank(row: dict):
        f = _forensics(row)
        return (STRENGTH_RANK[_strength(f)], len(_methods(f)), str(row.get("published") or ""))

    return max(group, key=rank)


def merge_cases(rows: list[dict]) -> list[dict]:
    """Collapse rows sharing a ``story_key`` into one representative row each."""
    return [_representative(g) for g in _story_groups(rows)]


def funnel(rows) -> dict:
    """Basis counts, each stage a subset of the previous (monotone)."""
    rows = list(rows)
    lines = len(rows)
    deduped = _core.collapse_rows_by_link(rows)
    with_forensics = [r for r in deduped if _forensics(r)]
    v3 = [r for r in with_forensics if _schema_version(_forensics(r)) >= V3_SCHEMA]
    verdict_true = [r for r in v3 if _forensics(r).get("is_insider_case") is True]
    method_bearing = [r for r in verdict_true if _methods(_forensics(r))]
    cases = merge_cases(method_bearing)
    industry_counts = {label: 0 for label in INDUSTRY_LABELS}
    employer_counts = {label: 0 for label in INDUSTRY_LABELS}
    for r in verdict_true:
        industry_counts[_core.resolve_industry(_forensics(r))] += 1
        employer_counts[_sector(_forensics(r), "employer")] += 1
    not_asked = sum(
        1
        for r in with_forensics
        if _schema_version(_forensics(r)) < V3_SCHEMA
        and _forensics(r).get("is_insider_case") is True
    )
    return {
        "lines": lines,
        "deduped_links": len(deduped),
        "with_forensics": len(with_forensics),
        "v3_tier": len(v3),
        "verdict_true": len(verdict_true),
        "method_bearing": len(method_bearing),
        "cases_after_story_merge": len(cases),
        "industry_counts_v3": industry_counts,
        "employer_counts_v3": employer_counts,
        "not_asked_pre_v3": not_asked,
    }


def _sector(f: dict, by: str) -> str:
    """The slicing sector for ``by``: victim's ``industry`` or the insider's employer."""
    if by == "employer":
        return _core.resolve_actor_employer_sector(f)
    return _core.resolve_industry(f)


def _other_axis(by: str) -> str:
    return "employer" if by == "victim" else "victim"


def industry_rows(rows, industry: str, *, by: str = "victim") -> list[dict]:
    """Deduped, v3-tier, verdict-true, method-bearing rows for one sector.

    ``by="victim"`` matches the victim organization's ``industry``;
    ``by="employer"`` matches the insider's own ``actor_employer_sector``.
    """
    want = (industry or "").strip().lower()
    out = []
    for r in _core.collapse_rows_by_link(rows):
        f = _forensics(r)
        if not _is_v3_verdict_true(f) or not _methods(f):
            continue
        if _sector(f, by) == want:
            out.append(r)
    return out


def cross_tab(cases: list[dict], industry: str, *, by: str, floor: int = SMALL_N_FLOOR) -> dict:
    """VICTIM × EMPLOYER for one slice: did the insider work for the victim?

    Rows are the OTHER axis's sector (employer sector in victim mode, victim
    industry in employer mode); columns are the relation to the slice
    sector: same / other / unknown. Counts of cases only; shares suppressed
    below ``floor``.
    """
    want = (industry or "").strip().lower()
    other = _other_axis(by)
    rows: dict[str, dict[str, int]] = {}
    totals = dict.fromkeys(RELATIONS, 0)
    for case in cases:
        label = _sector(_forensics(case), other)
        if label == "unknown":
            relation = "unknown"
        elif label == want:
            relation = "same"
        else:
            relation = "other"
        cell = rows.setdefault(label, dict.fromkeys(RELATIONS, 0))
        cell[relation] += 1
        totals[relation] += 1
    n = len(cases)
    table = []
    for label, cell in rows.items():
        table.append({"sector": label, **cell, "cases": sum(cell.values())})
    table.sort(key=lambda c: (c["sector"] == "unknown", -c["cases"], c["sector"]))
    return {
        "by": by,
        "row_axis": other,
        "cases": n,
        "rows": table,
        "totals": totals,
        "shares_pct": {k: (round(100 * v / n) if n >= floor else None) for k, v in totals.items()},
    }


def _profile_key(profile: tuple[str, str]) -> str:
    return f"{profile[0]} · {profile[1]}"


def profile_table(
    cases: list[dict], floor: int = SMALL_N_FLOOR, *, rows_by_case=None
) -> list[dict]:
    """One row per (function, employment_state), cases desc, unknown/unknown last.

    ``rows_by_case`` maps a representative row's link to the number of raw
    rows its story merged; absent, every case counts as one row.
    """
    total = len(cases)
    cells: dict[tuple[str, str], dict] = {}
    for case in cases:
        f = _forensics(case)
        profile = _profile(f)
        cell = cells.setdefault(
            profile,
            {
                "function": profile[0],
                "employment_state": profile[1],
                "cases": 0,
                "rows": 0,
                "adjudicated_admitted": 0,
                "alleged": 0,
                "reported_unclear": 0,
            },
        )
        cell["cases"] += 1
        cell["rows"] += (rows_by_case or {}).get(str(case.get("link") or ""), 1)
        cell[STRENGTH_KEYS[_strength(f)]] += 1
    table = []
    for profile, cell in cells.items():
        cell["share_pct"] = round(100 * cell["cases"] / total) if total >= floor else None
        if profile[1] == DEFAULTED_EMPLOYMENT_STATE:
            cell["note"] = "(default fill)"
        table.append(cell)
    table.sort(
        key=lambda c: (
            (c["function"], c["employment_state"]) == UNKNOWN_PROFILE,
            -c["cases"],
            c["function"],
            c["employment_state"],
        )
    )
    return table


def motive_counts(cases: list[dict]) -> dict[str, dict[str, int]]:
    """Per profile: how many cases carried each MOTIVE (MT-*) technique."""
    out: dict[str, Counter] = defaultdict(Counter)
    for case in cases:
        f = _forensics(case)
        key = _profile_key(_profile(f))
        seen = set()
        for tech in f.get("candidate_technique_ids") or []:
            tech = str(tech).upper().strip()
            if tech and tech not in seen and _core.technique_theme(tech) == "motive":
                seen.add(tech)
                out[key][tech] += 1
    return {k: dict(v.most_common()) for k, v in out.items()}


def posture_mix(cases: list[dict]) -> dict[str, dict[str, int]]:
    """Per profile: the legal posture of each case's document."""
    out: dict[str, Counter] = defaultdict(Counter)
    for case in cases:
        f = _forensics(case)
        out[_profile_key(_profile(f))][_posture(f)] += 1
    return {k: dict(v.most_common()) for k, v in out.items()}


def _table_bundle(rows: list[dict], floor: int) -> dict:
    groups = _story_groups(rows)
    cases = [_representative(g) for g in groups]
    rows_by_case = {str(c.get("link") or ""): len(g) for c, g in zip(cases, groups, strict=True)}
    return {
        "cases": len(cases),
        "rows": len(rows),
        "profiles": profile_table(cases, floor, rows_by_case=rows_by_case),
        "motives": motive_counts(cases),
        "postures": posture_mix(cases),
    }


def build_report(
    rows, *, industry: str, by: str = "victim", floor: int = SMALL_N_FLOOR, top: int = 15
) -> dict:
    """The whole report as a JSON-safe dict of labels and counts only."""
    if by not in MODES:
        raise ValueError(f"by must be one of {MODES}, got {by!r}")
    rows = list(rows)
    industry = (industry or "").strip().lower()
    sliced = industry_rows(rows, industry, by=by)
    report = {
        "industry": industry,
        "by": by,
        "floor": floor,
        "top": top,
        "funnel": funnel(rows),
        "industry_table": _table_bundle(sliced, floor),
        "unknown_pool_table": _table_bundle(industry_rows(rows, "unknown", by=by), floor),
        "cross_tab": cross_tab(merge_cases(sliced), industry, by=by, floor=floor),
        "reading_rules": list(EMPLOYER_READING_RULES if by == "employer" else READING_RULES),
    }
    return report


def to_json(report: dict) -> dict:
    return json.loads(json.dumps(report, sort_keys=True))


def _render_table(bundle: dict, top: int, out: list[str]) -> None:
    out.append(
        f"Cases: **{bundle['cases']}** (from {bundle['rows']} rows after story merge)."
        " CASES counts one per story; ROWS counts every document behind it."
    )
    out.append("")
    if not bundle["profiles"]:
        out.append(
            "_No cases yet. Rows appear here once the enricher has adjudicated a v3"
            " insider case in this industry with at least one extracted method._"
        )
        return
    out.append(
        "| Function | Employment state | Cases | Rows | Adjudicated/admitted | Alleged"
        " | Reported/unclear | Share |"
    )
    out.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for cell in bundle["profiles"][:top]:
        share = "n/a" if cell["share_pct"] is None else f"{cell['share_pct']}%"
        state = cell["employment_state"] + (f" {cell['note']}" if cell.get("note") else "")
        out.append(
            f"| {cell['function']} | {state} | {cell['cases']} | {cell['rows']} |"
            f" {cell['adjudicated_admitted']} | {cell['alleged']} |"
            f" {cell['reported_unclear']} | {share} |"
        )
    hidden = len(bundle["profiles"]) - min(top, len(bundle["profiles"]))
    if hidden > 0:
        out.append("")
        out.append(f"_{hidden} smaller profile row(s) not shown (--top {top})._")


def _render_counter_block(
    title: str, per_profile: dict, top: int, out: list[str], empty: str
) -> None:
    out.append(f"## {title}")
    out.append("")
    if not per_profile:
        out.append(f"_{empty}_")
        out.append("")
        return
    out.append("| Profile | Counts (cases) |")
    out.append("|---|---|")
    for key, counts in list(per_profile.items())[:top]:
        mix = ", ".join(f"{k} ×{n}" for k, n in counts.items()) or "—"
        out.append(f"| {key} | {mix} |")
    out.append("")


def _render_cross_tab(report: dict, out: list[str]) -> None:
    ct = report["cross_tab"]
    by = ct["by"]
    ind = report["industry"]
    labels = RELATION_LABELS[by]
    out.append("## Victim × employer")
    out.append("")
    if by == "victim":
        out.append(
            f"Where the insiders in the **{ind}** victim cases worked. Rows are the insider's"
            " own employer's sector (v3 additive field actor_employer_sector); columns say"
            " whether that employer was the victim itself, a firm in another sector, or not"
            " on record (never asked, or the model answered unknown — this script does not"
            " tell those apart)."
        )
    else:
        out.append(
            f"Who the insiders employed in **{ind}** hit. Rows are the victim organization's"
            " sector; columns say whether the victim was the insider's own employer, a firm"
            " in another sector, or unknown to the enricher."
        )
    out.append("")
    out.append(f"Cases: **{ct['cases']}** (after story merge).")
    out.append("")
    if not ct["rows"]:
        out.append(
            "_No cases yet. Rows appear here once the enricher has adjudicated a v3 insider"
            " case in this slice with at least one extracted method._"
        )
        return
    axis = "Employer sector" if ct["row_axis"] == "employer" else "Victim sector"
    out.append(f"| {axis} | {labels['same']} | {labels['other']} | {labels['unknown']} | Cases |")
    out.append("|---|---:|---:|---:|---:|")
    for cell in ct["rows"]:
        out.append(
            f"| {cell['sector']} | {cell['same']} | {cell['other']} | {cell['unknown']} |"
            f" {cell['cases']} |"
        )
    out.append("")
    parts = []
    for key in RELATIONS:
        pct = ct["shares_pct"][key]
        share = "n/a" if pct is None else f"{pct}%"
        parts.append(f"{labels[key].upper()} ×{ct['totals'][key]} ({share})")
    out.append(" · ".join(parts))


def render(report: dict) -> str:
    fn = report["funnel"]
    ind = report["industry"]
    top = report["top"]
    by = report.get("by", "victim")
    employer = by == "employer"
    if employer:
        out = [f"# Actor profiles — insider's employer: {ind}", ""]
        out.append(
            "Who the insiders were, by job function and employment state, in cases where"
            f" the insider's OWN employer was in **{ind}**, whoever the victim was."
            " Roles, never individuals."
        )
    else:
        out = [f"# Actor profiles — industry: {ind}", ""]
        out.append(
            "Who the insiders were, by job function and employment state, in cases where"
            f" the victim organization was in **{ind}**. Roles, never individuals."
        )
    out.append("")
    out.append("## Basis funnel")
    out.append("")
    out.append("| Stage | Rows |")
    out.append("|---|---:|")
    for label, key in (
        ("JSONL lines", "lines"),
        ("Deduped links (last line wins)", "deduped_links"),
        ("With forensics", "with_forensics"),
        (f"Schema v{V3_SCHEMA}+ tier (industry was asked)", "v3_tier"),
        ("Verdict-true insider cases", "verdict_true"),
        ("With ≥1 extracted method", "method_bearing"),
        ("Cases after story merge", "cases_after_story_merge"),
    ):
        out.append(f"| {label} | {fn[key]} |")
    out.append("")
    out.append(
        f"Verdict-true rows below schema v{V3_SCHEMA} (industry NOT ASKED, excluded from every"
        f" industry count): **{fn['not_asked_pre_v3']}**"
    )
    out.append("")
    if employer:
        out.append(f"Insider's employer sector of verdict-true v{V3_SCHEMA} rows:")
        out.append("")
        out.append("| Employer sector | Rows |")
        out.append("|---|---:|")
        counts = fn["employer_counts_v3"]
    else:
        out.append(f"Industry of verdict-true v{V3_SCHEMA} rows:")
        out.append("")
        out.append("| Industry | Rows |")
        out.append("|---|---:|")
        counts = fn["industry_counts_v3"]
    for label, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append(f"| {label} | {n} |")
    out.append("")
    slice_label = f"insider's employer: {ind}" if employer else ind
    out.append(f"## Profiles — {slice_label}")
    out.append("")
    _render_table(report["industry_table"], top, out)
    out.append("")
    if employer:
        out.append("## Unknown pool — employer unknown / not asked (v3) (contamination check)")
    else:
        out.append("## Unknown pool — industry: unknown (v3 contamination check)")
    out.append("")
    _render_table(report["unknown_pool_table"], top, out)
    out.append("")
    _render_counter_block(
        f"Motives by profile — {slice_label}",
        report["industry_table"]["motives"],
        top,
        out,
        "No motive (MT-*) technique on any case yet.",
    )
    _render_counter_block(
        f"Legal posture by profile — {slice_label}",
        report["industry_table"]["postures"],
        top,
        out,
        "No cases yet.",
    )
    out.append("## Reading rules")
    out.append("")
    for rule in report["reading_rules"]:
        out.append(f"- {rule}")
    # Appended AFTER the reading rules on purpose: victim-mode output stays a
    # byte-identical prefix of the pre-cross-tab report (test-pinned).
    out.append("")
    _render_cross_tab(report, out)
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("corpus", help="path to processed articles.jsonl")
    ap.add_argument(
        "--industry", default="financial-services", help="victim sector (INDUSTRY_LABELS)"
    )
    ap.add_argument(
        "--by",
        choices=MODES,
        default="victim",
        help="slice on the victim's sector (default) or the insider's own employer's sector",
    )
    ap.add_argument("--top", type=int, default=15, help="rows per table")
    ap.add_argument("--json", dest="json_out", default=None, help="also write the report as JSON")
    args = ap.parse_args(argv)
    if args.industry.strip().lower() not in INDUSTRY_LABELS:
        print(
            f"unknown industry {args.industry!r}; one of {', '.join(INDUSTRY_LABELS)}",
            file=sys.stderr,
        )
        return 2
    report = build_report(
        _core.iter_jsonl_rows(args.corpus), industry=args.industry, by=args.by, top=args.top
    )
    print(render(report))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(to_json(report), fh, indent=1, sort_keys=True)
        print(f"\nWrote {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
