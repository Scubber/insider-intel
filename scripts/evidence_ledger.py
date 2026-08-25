#!/usr/bin/env python3
"""Insider Evidence Ledger — corpus-wide forensic technique aggregation.

Thin CLI over shared/utils/evidence.py::build_evidence_ledger (the same core
the API's GET /evidence/ledger serves): reads the processed-corpus JSONL and
prints the markdown report (optional JSON sidecar). Pure stdlib; read-only;
no LLM spend. See the core module for the counting rules.

Usage: evidence_ledger.py CORPUS.jsonl [--json ledger.json] [--top 25]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

# Load the aggregation core as a standalone module FILE, not via the
# shared.utils package: the package __init__ imports entity/ITM modules that
# need pydantic, which the bare Actions runner does not have. The core itself
# is pure stdlib by contract.
_CORE_PATH = Path(__file__).resolve().parent.parent / "shared" / "utils" / "evidence.py"
_spec = importlib.util.spec_from_file_location("evidence_core", _CORE_PATH)
_core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core)
CHANNELS = _core.CHANNELS
build_evidence_ledger = _core.build_evidence_ledger


def _iter_rows(path: str):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def render_markdown(ledger: dict) -> str:
    s = ledger["strength_totals"]
    b = ledger.get("basis") or {}
    q = ledger.get("quote_grounding") or {}
    p = ledger.get("posture") or {}
    out = []
    out.append("# Insider Evidence Ledger")
    out.append("")
    out.append(
        f"Corpus rows: **{ledger['total_rows']}** · insider cases with extracted methods: "
        f"**{ledger['enriched_cases']}** (adjudicated/admitted: {s['adjudicated_admitted']}, "
        f"alleged: {s['alleged']}, reported/unclear: {s['reported_unclear']})"
    )
    out.append("")
    mix = ", ".join(f"{m}×{n}" for m, n in (b.get("model_mix") or {}).items()) or "n/a"
    share = q.get("verbatim_share_pct")
    out.append(
        f"_Generated {ledger.get('generated_at', '?')} · verdict-gated basis: "
        f"{b.get('corpus_rows', ledger['total_rows'])} corpus rows → "
        f"{b.get('enriched_rows', 0)} enriched → {b.get('verdict_true_rows', 0)} "
        f"adjudicated-insider; excluded method-bearing rows: "
        f"{b.get('excluded_non_insider', 0)} non-insider + "
        f"{b.get('excluded_no_verdict', 0)} no-verdict. "
        f"Posture-capped cases: {p.get('capped_cases', 0)}. "
        f"Verbatim-verified quotes: {share if share is not None else 'n/a'}"
        f"{'%' if share is not None else ''} of {q.get('quoted_methods', 0)}. "
        f"Enrichment models: {mix}_"
    )
    out.append("")
    out.append("> Counting rules: only rows adjudicated `is_insider_case=True` contribute.")
    out.append("> A case's strength is the STRONGEST claim_status any of its methods")
    out.append("> carries, capped by the document's legal_posture — a complaint/indictment")
    out.append("> can never mint an adjudicated case. Adjudicated/admitted methods are")
    out.append("> ground truth; alleged is a complaint's theory. Never read the two")
    out.append("> columns as equivalent.")

    findings = ledger.get("findings") or []
    if findings:
        out.append("")
        out.append("## Findings")
        out.append("")
        out.append("Derived from the aggregates below on every run — nothing here is stored.")
        for f in findings:
            out.append("")
            out.append(f"### {f['rank']}. {f['title']}")
            out.append("")
            out.append(f"**{f['stat']}** {f['stat_label']}")
            out.append("")
            out.append(f["takeaway"])
            if f.get("recommendations"):
                out.append("")
                for rec in f["recommendations"]:
                    out.append(f"- {rec}")
            out.append("")
            out.append(f"_Method: {f['method']}_")

    out.append("")
    out.append("## 1 · Technique frequency (ITM)")
    out.append("")
    out.append("| Technique | Cases | Adjud./adm. | Alleged | Exemplars |")
    out.append("|---|---|---|---|---|")
    for t in ledger["techniques"]:
        out.append(
            f"| {t['id']} | {t['cases']} | {t['adjudicated_admitted']} | "
            f"{t['alleged']} | {'; '.join(t['exemplars'])} |"
        )

    out.append("")
    out.append("## 2 · Evidence trail — where case evidence lives")
    out.append("")
    out.append("NOT necessarily how each case was detected. Each row is a record class the")
    out.append("described behavior touches: `mech` = the act NECESSARILY produced this")
    out.append("record (defensible); `inferred` = where an analyst would look (a hunting")
    out.append("lead, not a detection fact). `e.g.` shows real artifact strings folded")
    out.append("into the family.")
    out.append("")
    out.append(
        "| Evidence record class | Cases | Adjud./adm. cases | mech | inferred "
        "| Top channels | e.g. |"
    )
    out.append("|---|---|---|---|---|---|---|")
    for a in ledger["detected_by"]:
        examples = "; ".join(a.get("examples") or [])
        out.append(
            f"| {a['artifact']} | {a['cases']} | {a['adjudicated_admitted_cases']} | "
            f"{a['mechanical_observables']} | {a.get('inferred_observables', 0)} | "
            f"{', '.join(a['top_channels'])} | {examples} |"
        )

    roles = ledger.get("roles") or {}
    out.append("")
    out.append("## 2b · WHO — actor profile (roles, never individuals)")
    out.append("")
    out.append(
        f"Role signal present for **{roles.get('known', 0)}** of "
        f"{ledger['enriched_cases']} cases. Percentages suppressed below "
        f"n={ledger.get('small_n_floor', 10)}."
    )
    for axis, title in (("function", "FUNCTION"), ("employment_state", "EMPLOYMENT STATE")):
        out.append("")
        out.append(f"**{title}**")
        out.append("")
        out.append("| Role | Cases | Adjud./adm. | Share |")
        out.append("|---|---|---|---|")
        for r in roles.get(axis, []):
            share = f"{r['share_pct']}%" if r.get("share_pct") is not None else "—"
            out.append(f"| {r['label']} | {r['cases']} | {r['adjudicated_admitted']} | {share} |")

    out.append("")
    out.append("## 3 · Channel coverage")
    out.append("")
    out.append("| Channel | Distinct cases with evidence in this channel |")
    out.append("|---|---|")
    for ch in CHANNELS:
        out.append(f"| {ch} | {ledger['channels'].get(ch, 0)} |")

    out.append("")
    out.append("## 4 · Coverage questions (program self-assessment)")
    out.append("")
    out.append("For each top artifact: can your program produce this on demand, and how")
    out.append("far back? Share shown is of adjudicated/admitted cases it evidenced.")
    out.append("")
    for a in ledger["detected_by"][:10]:
        share = f"{a['adjudicated_share']}%" if a["adjudicated_share"] is not None else "n/a"
        out.append(
            f"- **{a['artifact']}** — touched by {a['cases']} case(s); {share} of "
            f"adjudicated/admitted cases. Can you produce this record class on demand?"
        )

    out.append("")
    out.append("## Appendix · Technique mix by filing year (CAVEAT: collection bias)")
    out.append("")
    out.append("> The corpus reflects OUR CourtListener query lexicon and sweep history —")
    out.append("> a shift below may be a change in what we collect, not in the world.")
    out.append("")
    out.append("| Year | Cases | Tracked techniques |")
    out.append("|---|---|---|")
    for year, bucket in ledger["by_year"].items():
        # A fixed technique set across every year, so a 0 is a real 0 rather
        # than "ranked below the cut that year".
        mix = ", ".join(f"{t}×{n}" for t, n in bucket["techniques"].items())
        out.append(f"| {year} | {bucket['cases']} | {mix} |")
    undated = ledger.get("by_year_undated_cases") or 0
    if undated:
        out.append("")
        out.append(f"_{undated} case(s) carry no usable date and are excluded from this table._")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--json", dest="json_out", default=None, help="Also write a JSON sidecar")
    ap.add_argument("--top", type=int, default=25, help="Rows per ranking table")
    args = ap.parse_args(argv)

    ledger = build_evidence_ledger(_iter_rows(args.corpus), top=args.top)
    print(render_markdown(ledger))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
