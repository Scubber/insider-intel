"""One-shot backfill: replay web/demo/articles.json into the raw store.

The Jul 2026 cloud cutover seeded the corpus from a fresh ingest, losing
articles that only existed in the historical local corpus behind the Pages
demo snapshot. The snapshot keeps every raw field (title/summary/link/
published/source), so replaying it through the normal pipeline regenerates
full processed rows. Dedupe against existing raw articles is by link.

Run inside the app container from the repo root, then `process --force`.
"""

from __future__ import annotations

import json
from pathlib import Path

from apps.aggregator.storage import JsonlArticleStore
from shared.schemas import RawArticle
from shared.settings import get_settings


def main() -> None:
    demo = json.loads(Path("web/demo/articles.json").read_text())
    rows = demo if isinstance(demo, list) else demo.get("results", demo.get("articles"))
    articles = [
        RawArticle(
            title=r["title"],
            link=r["link"],
            summary=r.get("summary") or "",
            published=r.get("published"),
            source_id=r.get("source_id") or "demo-snapshot",
            source_name=r.get("source_name") or "Demo snapshot backfill",
            channel=r.get("channel") if r.get("channel") in {"news", "filings", "tips"} else "news",
        )
        for r in rows
        if r.get("link") and r.get("title")
    ]
    store = JsonlArticleStore(get_settings().raw_articles_path)
    saved = store.save(articles)
    print(f"snapshot rows={len(rows)} replayed={len(articles)} newly_saved={saved}")


if __name__ == "__main__":
    main()
