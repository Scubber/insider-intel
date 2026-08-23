#!/usr/bin/env python3
"""Keep-best merge of two corpus generations to recover gutted enrichments.

A destructive re-enrich (clear-then-enrich) can replace a filing's rich stored
record with an empty one when the source text isn't archived (e.g. Sony/Udio:
the drain cleared its note, then re-enrichment over docket-metadata-only text
produced methods=0). This script repairs that WITHOUT any LLM spend by donating
the richer record from a pre-drain generation of ``articles.jsonl``.

It operates on raw JSONL (no app deps): union both files by ``link``. Rows only
in the current file are always kept; rows only in the donor are RE-ADDED (a
stale-generation overwrite deletes whole rows — the 2026-08-23 incident); a row
present in both is replaced only when the donor's record is strictly richer and
the current one is gutted, so current improvements are never clobbered — and
either way the two sides' ``enrichment_history`` lists are unioned (signature-
deduped, append-only law) so no generation is lost; the app's select-best
projection recomputes over the union at the row's next enrichment pass. Every
restore and re-add is reported.

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


def _history_signature(rec: dict) -> tuple:
    """Stdlib mirror of forensics._enrichment_signature over a raw history dict."""
    forensics = rec.get("forensics") or {}
    try:
        conf = round(float(forensics.get("confidence") or 0.0), 4)
    except (TypeError, ValueError):
        conf = 0.0
    return (
        rec.get("model"),
        rec.get("schema_version"),
        (rec.get("ai_summary") or "").strip(),
        len(forensics.get("methods") or []),
        conf,
    )


def _union_history(kept: dict, other: dict) -> bool:
    """Fold ``other``'s enrichment_history records into ``kept``'s, dedup by
    signature (append-only law: a generation present on either side survives).
    Returns True when ``kept`` gained at least one record. The top-level
    projection is left alone — the app recomputes select-best over the unioned
    history the next time the row passes through enrichment."""
    other_hist = other.get("enrichment_history") or []
    if not other_hist:
        return False
    kept_hist = list(kept.get("enrichment_history") or [])
    seen = {_history_signature(r) for r in kept_hist}
    gained = False
    for rec in other_hist:
        if _history_signature(rec) not in seen:
            kept_hist.append(rec)
            seen.add(_history_signature(rec))
            gained = True
    if gained:
        kept["enrichment_history"] = kept_hist
    return gained


def merge(
    current: list[dict], donor: list[dict]
) -> tuple[list[dict], list[dict], list[dict], int]:
    """Return (merged_rows, restored_reports, readded_reports, history_merged).

    ``restored_reports`` lists rows where the donor's richer record replaced a
    gutted current one. ``readded_reports`` lists donor rows absent from the
    current file entirely (a stale-generation overwrite drops whole rows —
    2026-08-23 incident — so the union re-adds them; the original script only
    walked current rows and silently abandoned these). ``history_merged``
    counts rows in both files whose enrichment_history records were unioned.
    """
    donor_by_link = {r.get("link"): r for r in donor if r.get("link")}
    current_links = {r.get("link") for r in current if r.get("link")}
    merged: list[dict] = []
    restored: list[dict] = []
    history_merged = 0
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
            kept, other = (dict(d), row) if don_s > cur_s and cur_gutted else (dict(row), d)
            if _union_history(kept, other):
                history_merged += 1
            if don_s > cur_s and cur_gutted:
                merged.append(kept)
                restored.append(
                    {
                        "link": link,
                        "title": (row.get("title") or "")[:80],
                        "cur_score": round(cur_s, 2),
                        "donor_score": round(don_s, 2),
                    }
                )
                continue
            merged.append(kept)
            continue
        merged.append(row)
    readded: list[dict] = []
    for row in donor:
        link = row.get("link")
        if link and link not in current_links:
            merged.append(row)
            readded.append({"link": link, "title": (row.get("title") or "")[:80]})
    return merged, restored, readded, history_merged


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--current", required=True, type=Path)
    ap.add_argument("--donor", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true", help="Report only; don't write --out")
    args = ap.parse_args(argv)

    current = _load(args.current)
    donor = _load(args.donor)
    merged, restored, readded, history_merged = merge(current, donor)

    print(f"current rows : {len(current)}")
    print(f"donor rows   : {len(donor)}")
    print(f"restored     : {len(restored)}")
    print(f"re-added     : {len(readded)}  (rows only the donor still had)")
    print(f"history-merged: {history_merged}  (enrichment_history unioned, projection untouched)")
    for r in restored:
        print(f"  RESTORE {r['link']}  ({r['cur_score']} -> {r['donor_score']})  {r['title']}")
    for r in readded:
        print(f"  READD   {r['link']}  {r['title']}")

    if args.dry_run:
        print("(dry-run: no file written)")
        return 0

    if not restored and not readded and not history_merged:
        print("(nothing to recover: no rows written; leaving current corpus untouched)")
        return 0

    with args.out.open("w", encoding="utf-8") as fh:
        for row in merged:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(merged)} rows -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
