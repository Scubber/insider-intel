#!/usr/bin/env python3
"""Count filings that a re-enrich drain would re-bill (read-only, no LLM).

Mirrors apps/aggregator/reenrich.py::select_missed_filings on raw JSONL so it
can run in a plain runner against a downloaded corpus. A filing is "stale"
(would be re-enriched) when it has a forensic record whose model != the target
OR whose schema_version is below the current clamp generation. Never-enriched
filings are reported separately (the normal budget-bounded backfill picks those
up gradually, not the drain).

Usage: count_stale_filings.py CORPUS.jsonl [--target claude-sonnet-5] [--schema 2]
"""

from __future__ import annotations

import argparse
import json
import sys


def _is_filing(row: dict) -> bool:
    sid = str(row.get("source_id") or "").lower()
    channel = row.get("channel")
    category = str(row.get("category") or "").lower()
    if "courtlistener" in sid or channel == "filings":
        return True
    return category in {"filings", "court", "recap"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--target", default="claude-sonnet-5")
    ap.add_argument("--schema", type=int, default=2)
    ap.add_argument(
        "--body-min",
        type=int,
        default=1500,
        help="clean_text chars at/above which a filing counts as having a real body",
    )
    args = ap.parse_args(argv)

    total = filings = enriched_on_target = stale_model = stale_schema = never_enriched = 0
    # Body coverage: a filing only carries the real document text when the free
    # RECAP archive had it (backfilled into clean_text). The rest are metadata
    # stubs (docket name/court/parties) whose body lives behind PACER.
    filings_with_body = filings_metadata_only = 0
    body_threshold = args.body_min
    with open(args.corpus, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            if not _is_filing(row):
                continue
            filings += 1
            body_len = len((row.get("clean_text") or "").strip())
            if body_len >= body_threshold:
                filings_with_body += 1
            else:
                filings_metadata_only += 1
            f = row.get("forensics")
            if not f:
                never_enriched += 1
                continue
            model = (f.get("model") or "").strip()
            try:
                schema = int(f.get("schema_version") or 1)
            except (TypeError, ValueError):
                schema = 1
            on_target = bool(args.target) and model == args.target
            on_schema = schema >= args.schema
            if on_target and on_schema:
                enriched_on_target += 1
            elif not on_target:
                stale_model += 1
            else:  # on target model but below current clamp schema
                stale_schema += 1

    stale_total = stale_model + stale_schema
    print(f"corpus rows           : {total}")
    print(f"filings               : {filings}")
    print(f"  with full body text  : {filings_with_body}  (clean_text >= {body_threshold} chars)")
    print(f"  metadata-only stub   : {filings_metadata_only}  (no free RECAP body; PACER-only)")
    print(f"  already current      : {enriched_on_target}")
    print(f"  STALE (wrong model)  : {stale_model}")
    print(f"  STALE (old clamp)    : {stale_schema}")
    print(f"  STALE total (drain would re-bill): {stale_total}")
    print(f"  never enriched (backfill picks up): {never_enriched}")
    print(f"DRAIN_STALE_TOTAL={stale_total}")
    print(f"BACKFILL_NEVER_ENRICHED={never_enriched}")
    print(f"FILINGS_WITH_BODY={filings_with_body}")
    print(f"FILINGS_METADATA_ONLY={filings_metadata_only}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
