"""Export the snapshot that feeds the standalone preview bundle.

Writes (under preview/data/, NOT web/ — the shipped site has no snapshot):
  preview/data/articles.json
  preview/data/itm.json
  preview/data/manifest.json

Run from insider-intel/:
  python -m scripts.export_demo_snapshot
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from apps.search.service import get_index, itm_catalog
from shared.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]
# Snapshot feeds the standalone preview bundle only — not shipped in web/.
DEMO_DIR = ROOT / "preview" / "data"


def _hit_dict(hit) -> dict:
    data = hit.model_dump(mode="json")
    # Compact topic blob for client-side topic_match (no full clean_text)
    return data


def main() -> None:
    settings = get_settings()
    index = get_index(settings.processed_articles_path, reload=True)
    catalog = itm_catalog()

    # Export the full local corpus (capped for Pages size safety). Prior 500-cap
    # dropped whole sources from the public Source dropdown.
    export_limit = min(max(index.size, 1), 3000)
    listed = index.list_articles(
        limit=export_limit,
        min_score=0.0,
        itm_alignment="all",
        topic_match=False,
    )
    articles = [_hit_dict(h) for h in listed.results]

    # Attach a short topic blob from the underlying processed article when present
    by_link = {a.link: a for a in index._articles}
    for row in articles:
        src = by_link.get(row["link"])
        if src is None:
            row["topic_blob"] = f"{row.get('title') or ''} {row.get('summary') or ''}".lower()
            continue
        blob = " ".join(
            [
                src.title or "",
                src.summary or "",
                (src.clean_text or "")[:4000],
            ]
        ).lower()
        row["topic_blob"] = blob

    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    articles_path = DEMO_DIR / "articles.json"
    itm_path = DEMO_DIR / "itm.json"
    manifest_path = DEMO_DIR / "manifest.json"

    articles_path.write_text(
        json.dumps({"total_indexed": len(articles), "articles": articles}, indent=None),
        encoding="utf-8",
    )
    itm_path.write_text(catalog.model_dump_json(), encoding="utf-8")
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "article_count": len(articles),
        "technique_count": len(catalog.techniques),
        "detection_count": len(catalog.detections),
        "prevention_count": len(catalog.preventions),
        "note": "Static GitHub Pages demo snapshot — not live ingest.",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"Wrote {articles_path} ({articles_path.stat().st_size // 1024} KiB)")
    print(f"Wrote {itm_path} ({itm_path.stat().st_size // 1024} KiB)")
    print(f"Wrote {manifest_path}")


if __name__ == "__main__":
    main()
