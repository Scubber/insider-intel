"""Export the slim boot snapshot the Pages artifact ships under web/data/.

Purpose: first-paint cache for the shipped UI while the Cloud Run API
cold-starts (~30s). NOT an offline responder — the UI renders it with a
CACHED badge and swaps to LIVE the moment the API answers. The output is
never committed to git; the pages workflow generates it into the artifact.

Writes to --out (default web/data/):
  articles.json  — ArticleListResponse-shaped: total_indexed/count/results
  meta.json      — {generated_at, indexed_articles, evidence_basis}
                   evidence_basis = the verdict-gated ledger's generation
                   stamp (row counts through the gate, model mix, verbatim
                   quote share) for the EVIDENCE-page staleness banner

Run from insider-intel/:
  python -m scripts.export_boot_snapshot --out web/data
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from apps.search.service import get_index
from shared.settings import get_settings
from shared.utils.evidence import build_evidence_ledger

# The snapshot IS the visit for most readers: at ~13 visits/day every request
# hits a Cloud Run cold start, so the stream painted from this file is what
# people actually read for the first 30s-2min while probeLiveApi waits out the
# boot. 800 slim card rows ≈ ~1 MB gzip (measured 2026-08-16: 500 rows =
# 0.64 MB, 1000 = 1.28 MB) — cheap enough to make first paint the product and
# the LIVE flip a minor top-up instead of the main event.
SNAPSHOT_LIMIT = 800
# Fields that dominate payload weight and aren't needed for the stream card
# are dropped: the heavy forensic detail (timeline, hunt seeds, observables,
# evidence quotes) and the legacy case_record (read-view detail, live-only).
# What survives is exactly what the stream card renders — the stamps
# (is_insider_case + context_kind), the posture badge, the analyst-note fact
# strip (caseFacts in web/app.js), and methods slimmed to action/claim_status
# (the METHODS fact line and the proof label). link/title kept so the slim
# record still validates as PerCaseForensics.
# tests/test_boot_snapshot.py mechanically checks this list against the
# fields web/app.js actually reads — extend BOTH when the card grows a read.
# The enrichment-provenance label (`enriched_by`, "Enriched by Claude Haiku
# 4.5") rides TOP-LEVEL on each hit — precomputed at projection time
# (shared/utils/model_display.py via apps/search/index.py::_to_hit), so it
# survives this slimming untouched and the raw model id stays out of the
# payload.
_KEEP_FORENSICS_KEYS = (
    "link",
    "title",
    "is_insider_case",
    "context_kind",
    "legal_posture",
    "actor_profile",
    "actor_role",
    "access_vector",
    "motive_signals",
    "exfil_channels",
    "timeframe",
    "detection",
    "outcome",
)
_KEEP_METHOD_KEYS = ("action", "claim_status")


def build_snapshot(limit: int = SNAPSHOT_LIMIT) -> tuple[dict, dict]:
    settings = get_settings()
    index = get_index(settings.processed_articles_path, reload=True)
    listed = index.list_articles(
        limit=limit,
        min_score=0.0,
        itm_alignment="all",
        topic_match=False,
    )
    results = []
    for hit in listed.results:
        row = hit.model_dump(mode="json")
        row.pop("case_record", None)
        forensics = row.pop("forensics", None)
        if isinstance(forensics, dict):
            slim = {k: forensics.get(k) for k in _KEEP_FORENSICS_KEYS}
            slim["methods"] = [
                {k: m.get(k) for k in _KEEP_METHOD_KEYS}
                for m in forensics.get("methods") or []
                if isinstance(m, dict)
            ]
            row["forensics"] = slim
        results.append(row)

    articles = {
        "total_indexed": index.size,
        "count": len(results),
        "results": results,
        "clusters": [],
    }
    # D-staleness: the ledger's generation basis rides in meta.json so the
    # cached first paint can render the EVIDENCE basis banner ("based on N
    # adjudicated-or-alleged cases as of DATE") before the live API answers.
    ledger = build_evidence_ledger(
        (
            {
                "link": a.link,
                "title": a.title,
                "published": a.published.isoformat() if a.published else "",
                "forensics": a.forensics.model_dump(mode="json") if a.forensics else None,
            }
            for a in index.articles
        ),
        top=1,
    )
    meta = {
        "generated_at": datetime.now(UTC).isoformat(),
        "indexed_articles": index.size,
        "evidence_basis": {
            "generated_at": ledger["generated_at"],
            **ledger["basis"],
            "quote_verbatim_share_pct": ledger["quote_grounding"]["verbatim_share_pct"],
        },
    }
    return articles, meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="web/data", help="Output directory")
    ap.add_argument("--limit", type=int, default=SNAPSHOT_LIMIT)
    args = ap.parse_args()

    articles, meta = build_snapshot(limit=args.limit)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    articles_path = out / "articles.json"
    meta_path = out / "meta.json"
    articles_path.write_text(json.dumps(articles, indent=None), encoding="utf-8")
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Wrote {articles_path} ({articles_path.stat().st_size // 1024} KiB)")
    print(f"Wrote {meta_path}")


if __name__ == "__main__":
    main()
