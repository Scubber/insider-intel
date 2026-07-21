#!/usr/bin/env python3
"""Keep-best merge of two corpus generations to recover gutted enrichments.

A destructive re-enrich (clear-then-enrich) can replace a filing's rich stored
record with an empty one when the source text isn't archived (e.g. Sony/Udio:
the drain cleared its note, then re-enrichment over docket-metadata-only text
produced methods=0). This script repairs that WITHOUT any LLM spend by donating
the richer record from a pre-drain generation of ``articles.jsonl``.

It operates on raw JSONL (no app deps): union both files by ``link``; for each
link keep whichever row has the richer enrichment. New rows present only in the
current file are always kept; a row is only replaced when the donor's record is
strictly richer, so current improvements (a genuinely better re-enrichment) are
never clobbered. Every restore is reported.

Usage:
  recover_gutted_rows.py --current CUR.jsonl --donor DONOR.jsonl \
      --out MERGED.jsonl [--dry-run]

Exit status is 0 on success; the restored-link report goes to stdout so the CI
step can surface exactly what changed before anything is uploaded.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _richness(row: dict) -> float:
    """Heuristic enrichment richness — higher means more analyst value.

    ai_summary presence dominates (a real note), then method count, then the
    forensic confidence as a tie-breaker. A gutted filing (no note, methods=0)
    scores ~0; a full record scores well above it.
    """
    ai = 1.0 if (row.get("ai_summary") or "").strip() else 0.0
    forensics = row.get("forensics") or {}
    methods = forensics.get("methods") or []
    try:
        conf = float(forensics.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    # ai_summary weighted heaviest, then each method, then confidence.
    return ai * 100.0 + len(methods) * 10.0 + conf


def _load(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # Preserve corpus robustness: a single bad line never sinks the run.
                continue
    return rows


def merge(current: list[dict], donor: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (merged_rows, restored_reports) preserving current's order.

    ``restored_reports`` is a list of {link, title, cur_score, donor_score} for
    every row where the donor's richer record replaced a poorer current one.
    """
    donor_by_link = {r.get("link"): r for r in donor if r.get("link")}
    merged: list[dict] = []
    restored: list[dict] = []
    for row in current:
        link = row.get("link")
        d = donor_by_link.get(link)
        if d is not None:
            cur_s = _richness(row)
            don_s = _richness(d)
            # Only restore when the donor is strictly richer AND the current row
            # actually regressed (no note or zero methods). Never downgrade a
            # current row that is itself rich (e.g. a good re-enrichment).
            cur_forensics = row.get("forensics") or {}
            cur_gutted = not (row.get("ai_summary") or "").strip() or not (
                cur_forensics.get("methods") or []
            )
            if don_s > cur_s and cur_gutted:
                merged.append(d)
                restored.append(
                    {
                        "link": link,
                        "title": (row.get("title") or "")[:80],
                        "cur_score": round(cur_s, 2),
                        "donor_score": round(don_s, 2),
                    }
                )
                continue
        merged.append(row)
    return merged, restored


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--current", required=True, type=Path)
    ap.add_argument("--donor", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true", help="Report only; don't write --out")
    args = ap.parse_args(argv)

    current = _load(args.current)
    donor = _load(args.donor)
    merged, restored = merge(current, donor)

    print(f"current rows : {len(current)}")
    print(f"donor rows   : {len(donor)}")
    print(f"restored     : {len(restored)}")
    for r in restored:
        print(f"  RESTORE {r['link']}  ({r['cur_score']} -> {r['donor_score']})  {r['title']}")

    if args.dry_run:
        print("(dry-run: no file written)")
        return 0

    if not restored:
        print("(nothing to restore: no rows written; leaving current corpus untouched)")
        return 0

    with args.out.open("w", encoding="utf-8") as fh:
        for row in merged:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(merged)} rows -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
