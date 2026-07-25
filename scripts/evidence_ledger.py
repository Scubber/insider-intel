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
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.utils.evidence import CHANNELS, build_evidence_ledger  # noqa: E402


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
    out = []
    out.append("# Insider Evidence Ledger")
    out.append("")
    out.append(
        f"Corpus rows: **{ledger['total_rows']}** · cases with extracted methods: "
        f"**{ledger['enriched_cases']}** (adjudicated/admitted: {s['adjudicated_admitted']}, "
        f"alleged: {s['alleged']}, reported/unclear: {s['reported_unclear']})"
    )
    out.append("")
    out.append("> Counting rule: a case's strength is the STRONGEST claim_status any of its")
    out.append("> methods carries. Adjudicated/admitted methods are ground truth; alleged is")
    out.append("> a complaint's theory. Never read the two columns as equivalent.")

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
    out.append("| Year | Cases w/ techniques | Top techniques |")
    out.append("|---|---|---|")
    for year, counts in ledger["by_year"].items():
        top = ", ".join(f"{t}×{n}" for t, n in counts.items())
        out.append(f"| {year} | {sum(counts.values())} | {top} |")
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
