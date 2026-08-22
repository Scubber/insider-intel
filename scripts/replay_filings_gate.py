"""Replay the filings spend gate (old vs new) over a stored processed corpus.

Measures the blast radius of the 2026-08 gate tightening (HANDOFF item 10)
BEFORE it decides real spend: the current body signal (a repeatedly-named
STRONG_INSIDER_OFFENSES phrase alone, OR an ITM alias AND an insider-framing
keyword — match markers stripped either way) versus the old one-alias bar,
and the resolve_channel fix that moves canlii-*/indiacourts-* rows from the
news gate to the filings gate.

Rows the strong-offense path re-admits were ALSO billed by the old one-alias
gate, so they never show as an old→new transition — the strong-only section
below breaks them out explicitly (tuned-bills vs what alias∧framing alone
would do), by phrase and adjudication. Compare SAVINGS against the prior
run's number to see the tune's cost.

Run where the corpus lives (sparky mounts it; a web sandbox cannot reach GCS):

    python scripts/replay_filings_gate.py [--path data/processed/articles.jsonl]

Reads only. Reports, per gate arm, how many rows would bill, and scores the
change against each row's own stored adjudication (forensics.is_insider_case):
rows the new gate blocks that were adjudicated non-insider are the savings;
blocked rows adjudicated insider are the false-negative cost the operator
reviews before merging the gate change.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.agents.summarize import (  # noqa: E402
    _body_has_insider_signal,
    strip_match_markers,
    strong_offense_hits,
)
from shared.schemas.articles import resolve_channel  # noqa: E402
from shared.utils.entities import find_framing_keywords, match_itm_techniques  # noqa: E402

FILING_MIN_CHARS = 1_500


def _old_gate(text: str, use_cases: list[str], alignment: str | None) -> bool:
    """The pre-2026-08 filings gate: body floor + any one in-body ITM alias."""
    body = (text or "").strip()
    if len(body) < FILING_MIN_CHARS:
        return False
    return bool(use_cases) or alignment == "insider" or bool(match_itm_techniques(body))


def _new_gate(text: str, use_cases: list[str], alignment: str | None) -> bool:
    """The current gate: markers stripped, strong offense OR alias∧framing."""
    body = strip_match_markers(text or "").strip()
    if len(body) < FILING_MIN_CHARS:
        return False
    return bool(use_cases) or alignment == "insider" or _body_has_insider_signal(body)


def _strong_only_hits(text: str, use_cases: list[str], alignment: str | None) -> tuple[str, ...]:
    """Offenses that are the SOLE reason this row bills — every other route
    (use_cases, alignment, alias∧framing) would block it. The tune's marginal
    reopening, invisible in the old→new transitions. (The first replay run
    counted rows that also cleared via use_cases/alignment, inflating the
    bucket — 2026-08-22 operator catch.)"""
    if use_cases or alignment == "insider":
        return ()
    body = strip_match_markers(text or "").strip()
    if len(body) < FILING_MIN_CHARS:
        return ()
    if bool(match_itm_techniques(body)) and bool(find_framing_keywords(body)):
        return ()
    return strong_offense_hits(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="data/processed/articles.jsonl")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"no corpus at {path} — run where the processed store is mounted")
        return 1

    totals: Counter[str] = Counter()
    transitions: Counter[str] = Counter()
    blocked_by_source: Counter[str] = Counter()
    strong_only: Counter[str] = Counter()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                totals["unparseable"] += 1
                continue
            source_id = row.get("source_id") or ""
            if resolve_channel(source_id) != "filings":
                # Includes rows the resolve_channel fix newly claims: count them.
                continue
            totals["filings_rows"] += 1
            text = row.get("clean_text") or ""
            use_cases = list(row.get("use_cases") or [])
            alignment = row.get("itm_alignment")
            old = _old_gate(text, use_cases, alignment)
            new = _new_gate(text, use_cases, alignment)
            totals["old_billed"] += old
            totals["new_billed"] += new

            forensics = row.get("forensics") or {}
            verdict = forensics.get("is_insider_case")
            v = {True: "insider", False: "non-insider"}.get(verdict, "unadjudicated")
            if old and not new:
                transitions[f"blocked:{v}"] += 1
                blocked_by_source[source_id.split("-")[0]] += 1
            elif new and not old:
                transitions[f"added:{v}"] += 1
            if new:
                for offense in _strong_only_hits(text, use_cases, alignment):
                    strong_only[f"{offense}:{v}"] += 1

    print(f"corpus: {path}")
    print(f"filings rows (new resolve_channel): {totals['filings_rows']}")
    print(f"old gate bills: {totals['old_billed']}   new gate bills: {totals['new_billed']}")
    print()
    print("transitions (old→new), by the row's own stored adjudication:")
    for key in sorted(transitions):
        print(f"  {key}: {transitions[key]}")
    fn = transitions.get("blocked:insider", 0)
    savings = transitions.get("blocked:non-insider", 0)
    print()
    print(f"SAVINGS (blocked, adjudicated non-insider): {savings}")
    print(f"FALSE-NEGATIVE COST (blocked, adjudicated insider): {fn}  ← review this")
    if blocked_by_source:
        print()
        print("blocked rows by source family:")
        for family, n in blocked_by_source.most_common():
            print(f"  {family}: {n}")
    if strong_only:
        print()
        print("strong-offense-only bills (no other route would bill them), by phrase:")
        for key in sorted(strong_only):
            print(f"  {key}: {strong_only[key]}")
        print("  ↑ ':insider' rows are the recaptured FNs; ':non-insider' rows are")
        print("    the tune's cost — compare SAVINGS against the prior run's number.")
    if totals.get("unparseable"):
        print(f"\nskipped unparseable lines: {totals['unparseable']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
