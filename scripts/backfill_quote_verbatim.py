"""Backfill evidence_quote_verbatim stamps over stored enrichments.

Deterministic and zero-LLM (audit item D5): replays
:func:`shared.schemas.forensics.stamp_quote_verbatim` against each enriched
row's stored ``clean_text`` — for the top-level projection AND every history
generation — so the whole corpus carries grounding verdicts, not just rows
enriched after the stamp shipped. Idempotent; rows whose stamps don't change
are not rewritten.

Usage (from the repo root, venv or container):
    python -m scripts.backfill_quote_verbatim [--processed-path PATH] [--dry-run]
"""

from __future__ import annotations

import argparse

from apps.aggregator.processed_storage import JsonlProcessedStore
from shared.schemas.forensics import stamp_quote_verbatim


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--processed-path", default="data/processed/articles.jsonl")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    store = JsonlProcessedStore(args.processed_path)
    changed = []
    quotes = verbatim = 0
    for row in store.load_all():
        targets = [row.forensics] if row.forensics is not None else []
        targets += [rec.forensics for rec in (row.enrichment_history or [])]
        if not targets:
            continue
        before = [[m.evidence_quote_verbatim for m in (f.methods or [])] for f in targets]
        for f in targets:
            stamp_quote_verbatim(f, row.clean_text or "")
        after = [[m.evidence_quote_verbatim for m in (f.methods or [])] for f in targets]
        for stamps in after:
            for s in stamps:
                if s is not None:
                    quotes += 1
                    verbatim += int(bool(s))
        if before != after:
            changed.append(row)

    print(
        f"quotes stamped: {quotes}; verbatim: {verbatim} "
        f"({100 * verbatim / max(quotes, 1):.1f}%); rows changed: {len(changed)}"
    )
    if changed and not args.dry_run:
        store.upsert(changed)
        print(f"upserted {len(changed)} row(s) (append; compacted next cycle)")
    elif changed:
        print("dry run — nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
