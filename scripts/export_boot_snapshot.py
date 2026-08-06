"""Export the slim boot snapshot the Pages artifact ships under web/data/.

Purpose: first-paint cache for the shipped UI while the Cloud Run API
cold-starts (~30s). NOT an offline responder — the UI renders it with a
CACHED badge and swaps to LIVE the moment the API answers. The output is
never committed to git; the pages workflow generates it into the artifact.

Writes to --out (default web/data/):
  articles.json  — ArticleListResponse-shaped: total_indexed/count/results
  meta.json      — {generated_at, indexed_articles}

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

# First paint only needs the top of the stream; live data replaces it.
SNAPSHOT_LIMIT = 200
# Fields that dominate payload weight and aren't needed for the stream card:
# the full forensic record (only is_insider_case drives the CONTEXT stamp)
# and the legacy case_record (read-view detail, live-only).
# link/title kept so the slim record still validates as PerCaseForensics.
_KEEP_FORENSICS_KEYS = ("link", "title", "is_insider_case")


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
            row["forensics"] = {k: forensics.get(k) for k in _KEEP_FORENSICS_KEYS}
        results.append(row)

    articles = {
        "total_indexed": index.size,
        "count": len(results),
        "results": results,
        "clusters": [],
    }
    meta = {
        "generated_at": datetime.now(UTC).isoformat(),
        "indexed_articles": index.size,
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
