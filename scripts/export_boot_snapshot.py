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
  tooling.json   — the exact GET /tooling payload (same tooling_rankings the
                   API serves — one source of truth, no drift) so the
                   snapshot-first ensureTooling() in web/app.js can paint the
                   TOOLING page instantly while the API cold-starts, then
                   swap to the live payload in the background

Run from insider-intel/:
  python -m scripts.export_boot_snapshot --out web/data
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from apps.aggregator.lane_health import read_lane_health
from apps.search.service import get_index, list_sources, tooling_rankings
from shared.itm.index import load_itm_index
from shared.settings import get_settings
from shared.utils.evidence import attach_catalog_titles, build_evidence_ledger

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


def _slim_hit(hit) -> dict:
    """Card-slim one article hit (forensics whitelisted, case_record dropped)."""
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
    return row


# The snapshot must mirror the UI's boot query EXACTLY or the live re-render
# replaces different content — which is the flash static-first exists to kill
# (web/app.js loadArticles: limit 75, SIG floor 0.30, insider alignment,
# grouped clusters). Keep in lockstep with loadArticles.
BOOT_QUERY = {
    "limit": 75,
    "min_score": 0.30,
    "itm_alignment": "insider",
    "topic_match": False,
    "group": True,
}


def build_snapshot(limit: int = SNAPSHOT_LIMIT) -> tuple[dict, dict, dict, list, dict]:
    settings = get_settings()
    index = get_index(settings.processed_articles_path, reload=True)
    listed = index.list_articles(**BOOT_QUERY)
    results = [_slim_hit(hit) for hit in listed.results]
    clusters = []
    for cluster in listed.clusters or []:
        c = cluster.model_dump(mode="json")
        c["primary"] = _slim_hit(cluster.primary)
        c["siblings"] = [_slim_hit(sib) for sib in cluster.siblings or []]
        clusters.append(c)

    articles = {
        "total_indexed": index.size,
        "count": len(results),
        "results": results,
        "clusters": clusters,
    }

    # SOURCE chip twin: the boot /sources query (same filters as the boot
    # stream), so the dropdown + counts paint without the API.
    sources = [
        s.model_dump(mode="json")
        for s in list_sources(
            settings.processed_articles_path,
            min_score=0.30,
            itm_alignment="insider",
        )
    ]
    # Ledger twin (masthead corpus counts + the MODUS OPERANDI footnote's
    # state.evidenceLedger) — the SAME payload GET /evidence/ledger serves;
    # meta.evidence_basis derives from it instead of a second aggregation.
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
        top=25,
    )
    # Same catalog join the API applies, so the cold-start paint reads the same
    # shape as the live payload (technique titles, the trend surface, and the
    # derived findings that replaced the old static findings.json).
    attach_catalog_titles(ledger, {t.id.upper(): t.title for t in load_itm_index().techniques})
    meta = {
        "generated_at": datetime.now(UTC).isoformat(),
        "indexed_articles": index.size,
        "evidence_basis": {
            "generated_at": ledger["generated_at"],
            **ledger["basis"],
            "quote_verbatim_share_pct": ledger["quote_grounding"]["verbatim_share_pct"],
        },
    }
    # Source-health footer while the API cold-starts: embed the lane-health
    # summary (refresh-job-written under state/; the pages workflow copies it
    # next to the corpus). Absent file → no key, UI just waits for the live
    # /lanes/health.
    lane_health = read_lane_health(settings.lane_health_path)
    if lane_health.get("generated_at"):
        meta["lane_health"] = {
            "generated_at": lane_health["generated_at"],
            **(lane_health.get("summary") or {}),
        }
    # TOOLING first paint: the exact GET /tooling payload, computed by the
    # SAME service function the API endpoint calls against the corpus loaded
    # above — no second aggregation path, no drift. The payload carries its
    # own generated_at + basis block, so the cached paint cites its true age
    # on every basis line without meta.json involvement.
    tooling = tooling_rankings(settings.processed_articles_path)
    return articles, meta, tooling, sources, ledger


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="web/data", help="Output directory")
    ap.add_argument("--limit", type=int, default=SNAPSHOT_LIMIT)
    args = ap.parse_args()

    articles, meta, tooling, sources, ledger = build_snapshot(limit=args.limit)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    payloads = {
        "articles.json": (articles, None),
        "meta.json": (meta, 2),
        "tooling.json": (tooling, None),
        "sources.json": (sources, None),
        "ledger.json": (ledger, None),
    }
    for name, (payload, indent) in payloads.items():
        path = out / name
        path.write_text(json.dumps(payload, indent=indent), encoding="utf-8")
        print(f"Wrote {path} ({path.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
