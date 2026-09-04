#!/usr/bin/env python3
"""Peer-set study: who the insiders were in cases NAMING a curated set of firms.

PRIVATE research lane (dispatched by .github/workflows/corpus-peerset.yml,
runnable locally): for one authored peer set (shared/data/peer_sets.json —
the operator's competitive set of retirement / insurance / asset-management
firms), count the cases whose stored text names at least one firm, then
profile those cases the way scripts/industry_actor_profiles.py profiles an
industry: job function × employment state, motives, legal postures, the
evidence-record classes and techniques the sliced ledger reports. Output is
COUNTS ONLY and goes to the private bucket export prefix; it is never a
public RESEARCH surface.

A firm MATCH is presence in the record, never fault: the firm may be the
victim, the plaintiff, the defendant's former employer, or named in passing.
Matching is case-insensitive, word-bounded, longest alias first (the
vendor_mentions.py idiom) over title + clean_text + summary + ai_summary —
never enrichment_history. Alias safety rules live in the JSON file and
tests/test_peer_set_profiles.py pins them ("Voyager" never credits Voya;
"principal amount" never credits Principal).

Roles, never individuals: every cell is a count; no name, title, link, quote
or summary enters the report — the tests seed unique tokens in every
free-text field and assert none survives.

Stdlib only — the bare Actions runner has no pydantic. The industry script
(and through it the evidence core) is loaded as a bare file.

Usage: peer_set_profiles.py CORPUS.jsonl [--peer-set retirement-insurance-asset-mgmt]
       [--json out.json] [--top 25]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_IND_PATH = _HERE / "industry_actor_profiles.py"
_spec = importlib.util.spec_from_file_location("industry_actor_profiles", _IND_PATH)
_ind = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ind)
_core = _ind._core

PEER_SETS_PATH = _HERE.parent / "shared" / "data" / "peer_sets.json"
SMALL_N_FLOOR = _ind.SMALL_N_FLOOR
V3_SCHEMA = _ind.V3_SCHEMA
FINANCIAL_SERVICES = "financial-services"
# Which stored fields a firm alias may match. enrichment_history is NOT here
# by contract: it holds every past generation, including superseded ones.
TEXT_FIELDS = ("title", "clean_text", "summary", "ai_summary")
WATCHLIST_FIRM = "voya"
WATCHLIST_DEFAULT = "Voya, Voya India"

READING_RULES = (
    "ROLES, NEVER INDIVIDUALS: every cell is a count of cases; no name, title, link,"
    " quote or summary is in this report. Firms appear by display name only.",
    "A FIRM MENTION is presence in the record, not fault: the firm may be the victim,"
    " the plaintiff, the defendant's former employer, or a bystander named in passing."
    " A count says the stored text names the firm; it never says the firm failed.",
    "INDUSTRY is the victim organization's sector, not the actor's employer: the"
    " OF WHICH FINANCIAL-SERVICES column counts named-firm cases where the victim sat in"
    " financial services, which is the nearest stored proxy for 'the firm was hit'.",
    'EMPLOYMENT STATE "current" is a default fill: the normalizer stamps it whenever a'
    " job function matched and the text carried no boundary language (former, resigned,"
    " contractor). Rows marked (default fill) measure the absence of that language, not"
    " tenure.",
    f"Percentages are suppressed below {SMALL_N_FLOOR} cases (share shows as n/a).",
    "Insider trading and embezzlement are literal COLLECTION LEXICON queries (CourtListener"
    " DEFAULT_QUERIES), so motive and posture counts reflect what the corpus went looking"
    " for, not the base rate.",
    "Firm matching is case-insensitive and word-bounded over title, body, summary and"
    " analyst note; enrichment history is never scanned. Alias safety rules live in"
    " shared/data/peer_sets.json (never bare Fidelity, Principal, Lincoln, Nationwide,"
    " Equitable, Empower, Vanguard).",
    "Case strength is the strongest method claim, capped by the document's legal posture:"
    " a complaint can never mint an adjudicated case.",
)

VOYA_CAVEAT = (
    f"COURTLISTENER_COMPANY_WATCHLIST defaults to {WATCHLIST_DEFAULT!r}"
    " (shared/settings.py), so every US filing naming Voya is collected BY CONSTRUCTION."
    " The Voya count is a collection artifact, not a base rate, and it cannot be compared"
    " with the other firms' counts, which come only from the general insider lexicon."
)


# ---------------------------------------------------------------------------
# Peer set + matcher


def load_peer_sets(path=None) -> dict:
    with open(path or PEER_SETS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def compile_firm_matcher(firms: dict) -> tuple[re.Pattern | None, dict[str, str]]:
    """One alternation over every alias, longest first; alias -> firm id."""
    homes: dict[str, str] = {}
    for firm_id, firm in firms.items():
        for alias in firm.get("aliases") or []:
            normalized = _normalize(str(alias))
            if normalized:
                homes.setdefault(normalized, firm_id)
    if not homes:
        return None, {}
    ordered = sorted(homes, key=lambda a: (-len(a), a))
    body = "|".join(r"\s+".join(re.escape(word) for word in alias.split()) for alias in ordered)
    return re.compile(rf"(?<![a-z0-9])(?:{body})(?![a-z0-9])"), homes


def row_text(row: dict) -> str:
    return _normalize(" ".join(str(row.get(k) or "") for k in TEXT_FIELDS))


def firms_named(row: dict, pattern, homes: dict[str, str]) -> set[str]:
    if pattern is None:
        return set()
    return {homes[_normalize(m.group(0))] for m in pattern.finditer(row_text(row))}


# ---------------------------------------------------------------------------
# Slice


def study_rows(rows, firms: dict) -> list[tuple[dict, set[str]]]:
    """Deduped, v3, verdict-true, method-bearing rows naming >=1 peer firm."""
    pattern, homes = compile_firm_matcher(firms)
    out = []
    for r in _core.collapse_rows_by_link(rows):
        f = _ind._forensics(r)
        if not _ind._is_v3_verdict_true(f) or not _ind._methods(f):
            continue
        named = firms_named(r, pattern, homes)
        if named:
            out.append((r, named))
    return out


def firm_table(matched: list[tuple[dict, set[str]]], firms: dict) -> list[dict]:
    """Per firm: story-merged cases, of which FS-victim, of which proven."""
    per_firm: dict[str, list[dict]] = {fid: [] for fid in firms}
    for row, named in matched:
        for fid in named:
            per_firm[fid].append(row)
    table = []
    for fid, rows in per_firm.items():
        cases = _ind.merge_cases(rows)
        fs = sum(
            1 for c in cases if _core.resolve_industry(_ind._forensics(c)) == FINANCIAL_SERVICES
        )
        proven = sum(
            1 for c in cases if _ind._strength(_ind._forensics(c)) == "adjudicated/admitted"
        )
        table.append(
            {
                "firm": fid,
                "display": firms[fid].get("display") or fid,
                "cases": len(cases),
                "rows": len(rows),
                "financial_services": fs,
                "adjudicated_admitted": proven,
            }
        )
    table.sort(key=lambda c: (-c["cases"], c["display"]))
    return table


def ledger_counts(rows: list[dict], top: int) -> dict:
    """Counts-only projection of the core ledger over the slice.

    The full ledger carries exemplars, artifact example strings, hunt seeds
    and behavior quotes — none of that may enter this report, so only the
    count fields are copied out.
    """
    ledger = _core.build_evidence_ledger(rows, top=top)
    return {
        "enriched_cases": ledger.get("enriched_cases", 0),
        "strength_totals": dict(ledger.get("strength_totals") or {}),
        "roles": {
            axis: [
                {
                    "label": r.get("label"),
                    "cases": r.get("cases"),
                    "adjudicated_admitted": r.get("adjudicated_admitted"),
                    "share_pct": r.get("share_pct"),
                }
                for r in (ledger.get("roles") or {}).get(axis) or []
            ]
            for axis in ("function", "employment_state")
        },
        "techniques": [
            {
                "id": t.get("id"),
                "theme": t.get("theme"),
                "cases": t.get("cases"),
                "adjudicated_admitted": t.get("adjudicated_admitted"),
                "alleged": t.get("alleged"),
            }
            for t in ledger.get("techniques") or []
        ],
        "detected_by": [
            {
                "artifact": a.get("artifact"),
                "cases": a.get("cases"),
                "adjudicated_admitted_cases": a.get("adjudicated_admitted_cases"),
                "mechanical_observables": a.get("mechanical_observables"),
                "inferred_observables": a.get("inferred_observables"),
                "top_channels": list(a.get("top_channels") or []),
            }
            for a in ledger.get("detected_by") or []
        ],
        "channels": dict(ledger.get("channels") or {}),
    }


def build_report(rows, *, peer_set: str, peer_sets=None, floor=SMALL_N_FLOOR, top=25) -> dict:
    rows = list(rows)
    catalog = peer_sets or load_peer_sets()
    spec = catalog["peer_sets"][peer_set]
    firms = spec["firms"]
    matched = study_rows(rows, firms)
    slice_rows = [r for r, _ in matched]
    voya_rows = [r for r, named in matched if WATCHLIST_FIRM in named]
    fn = _ind.funnel(rows)
    fn["peer_set_matched"] = len(slice_rows)
    fn["peer_set_cases_after_story_merge"] = len(_ind.merge_cases(slice_rows))
    firm_mix = Counter(len(named) for _, named in matched)
    report = {
        "peer_set": peer_set,
        "label": spec.get("label") or peer_set,
        "firm_count": len(firms),
        "floor": floor,
        "top": top,
        "funnel": fn,
        "firm_table": firm_table(matched, firms),
        "firms_per_case": {str(k): v for k, v in sorted(firm_mix.items())},
        "peer_table": _ind._table_bundle(slice_rows, floor),
        "ledger": ledger_counts(slice_rows, top),
        "voya_appendix": {
            "watchlist_default": WATCHLIST_DEFAULT,
            "caveat": VOYA_CAVEAT,
            "table": _ind._table_bundle(voya_rows, floor),
        },
        "reading_rules": list(READING_RULES),
    }
    return report


def to_json(report: dict) -> dict:
    return json.loads(json.dumps(report, sort_keys=True))


# ---------------------------------------------------------------------------
# Render


def render(report: dict) -> str:
    fn = report["funnel"]
    top = report["top"]
    out = [f"# Peer-set study — {report['label']}", ""]
    out.append(
        f"Who the insiders were in cases whose stored text names at least one of the"
        f" **{report['firm_count']}** firms in peer set `{report['peer_set']}`."
        " Roles, never individuals. PRIVATE export — not a public research surface."
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
        (f"Schema v{V3_SCHEMA}+ tier", "v3_tier"),
        ("Verdict-true insider cases", "verdict_true"),
        ("With ≥1 extracted method", "method_bearing"),
        ("Peer-set matched (names ≥1 firm)", "peer_set_matched"),
        ("Peer-set cases after story merge", "peer_set_cases_after_story_merge"),
    ):
        out.append(f"| {label} | {fn[key]} |")
    out.append("")
    out.append(
        f"Verdict-true rows below schema v{V3_SCHEMA} (not asked their industry; still"
        f" excluded from the study, which is v{V3_SCHEMA}-gated): **{fn['not_asked_pre_v3']}**"
    )
    out.append("")
    mix = ", ".join(f"{k} firm(s) ×{v}" for k, v in report["firms_per_case"].items()) or "—"
    out.append(f"Firms named per matched row: {mix}")
    out.append("")
    out.append("## Firm mentions")
    out.append("")
    out.append(
        "MENTIONED means the stored text names the firm — presence in the record,"
        " never fault or effectiveness. One case can name several firms."
    )
    out.append("")
    out.append("| Firm | Cases | Rows | Of which financial-services | Of which proven |")
    out.append("|---|---:|---:|---:|---:|")
    for cell in report["firm_table"]:
        out.append(
            f"| {cell['display']} | {cell['cases']} | {cell['rows']} |"
            f" {cell['financial_services']} | {cell['adjudicated_admitted']} |"
        )
    out.append("")
    out.append("## Profiles — pooled peer set")
    out.append("")
    _ind._render_table(report["peer_table"], top, out)
    out.append("")
    _ind._render_counter_block(
        "Motives by profile — pooled peer set",
        report["peer_table"]["motives"],
        top,
        out,
        "No motive (MT-*) technique on any case yet.",
    )
    _ind._render_counter_block(
        "Legal posture by profile — pooled peer set",
        report["peer_table"]["postures"],
        top,
        out,
        "No cases yet.",
    )
    _render_ledger(report["ledger"], out)
    out.append("## Appendix — Voya-only rows")
    out.append("")
    out.append(f"**Caveat.** {report['voya_appendix']['caveat']}")
    out.append("")
    _ind._render_table(report["voya_appendix"]["table"], top, out)
    out.append("")
    out.append("## Reading rules")
    out.append("")
    for rule in report["reading_rules"]:
        out.append(f"- {rule}")
    return "\n".join(out)


def _render_ledger(ledger: dict, out: list[str]) -> None:
    out.append("## Sliced evidence ledger — counts only")
    out.append("")
    st = ledger["strength_totals"]
    out.append(
        f"Verdict-true cases with methods: **{ledger['enriched_cases']}** —"
        f" adjudicated/admitted {st.get('adjudicated_admitted', 0)},"
        f" alleged {st.get('alleged', 0)}, reported/unclear {st.get('reported_unclear', 0)}."
    )
    out.append("")
    for axis, title in (
        ("function", "Who — job function"),
        ("employment_state", "Who — employment state"),
    ):
        out.append(f"### {title}")
        out.append("")
        rows = ledger["roles"][axis]
        if not rows:
            out.append("_No cases yet._")
            out.append("")
            continue
        out.append("| Label | Cases | Adjudicated/admitted | Share |")
        out.append("|---|---:|---:|---:|")
        for r in rows:
            share = "n/a" if r["share_pct"] is None else f"{r['share_pct']}%"
            out.append(f"| {r['label']} | {r['cases']} | {r['adjudicated_admitted']} | {share} |")
        out.append("")
    out.append("### Techniques")
    out.append("")
    if not ledger["techniques"]:
        out.append("_No techniques yet._")
    else:
        out.append("| Technique | Theme | Cases | Adjudicated/admitted | Alleged |")
        out.append("|---|---|---:|---:|---:|")
        for t in ledger["techniques"]:
            out.append(
                f"| {t['id']} | {t['theme']} | {t['cases']} | {t['adjudicated_admitted']} |"
                f" {t['alleged']} |"
            )
    out.append("")
    out.append("### Evidence-record classes touched")
    out.append("")
    if not ledger["detected_by"]:
        out.append("_No observables yet._")
    else:
        out.append(
            "| Record class | Cases | Adjudicated/admitted | Mechanical | Inferred | Channels |"
        )
        out.append("|---|---:|---:|---:|---:|---|")
        for a in ledger["detected_by"]:
            out.append(
                f"| {a['artifact']} | {a['cases']} | {a['adjudicated_admitted_cases']} |"
                f" {a['mechanical_observables']} | {a['inferred_observables']} |"
                f" {', '.join(a['top_channels']) or '—'} |"
            )
    out.append("")
    channels = ", ".join(f"{k} ×{v}" for k, v in ledger["channels"].items() if v) or "—"
    out.append(f"Channels: {channels}")
    out.append("")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("corpus", help="path to processed articles.jsonl")
    ap.add_argument("--peer-set", default="retirement-insurance-asset-mgmt")
    ap.add_argument("--top", type=int, default=25, help="rows per table")
    ap.add_argument("--json", dest="json_out", default=None, help="also write the report as JSON")
    args = ap.parse_args(argv)
    catalog = load_peer_sets()
    if args.peer_set not in catalog["peer_sets"]:
        print(
            f"unknown peer set {args.peer_set!r}; one of {', '.join(catalog['peer_sets'])}",
            file=sys.stderr,
        )
        return 2
    report = build_report(
        _core.iter_jsonl_rows(args.corpus), peer_set=args.peer_set, peer_sets=catalog, top=args.top
    )
    print(render(report))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(to_json(report), fh, indent=1, sort_keys=True)
        print(f"\nWrote {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
