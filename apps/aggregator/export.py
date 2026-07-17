"""One-way corporate export: NDJSON + manifest (no corp credentials inbound)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apps.aggregator.process_pipeline import DEFAULT_PROCESSED_PATH
from apps.aggregator.processed_storage import JsonlProcessedStore
from shared.schemas import ProcessedArticle

logger = logging.getLogger(__name__)

EXPORT_SCHEMA_VERSION = "insider-intel.export.v2"
DEFAULT_EXPORT_DIR = "dist/export"


def article_to_export_row(article: ProcessedArticle) -> dict[str, Any]:
    """Flatten a processed article for corporate consumers."""
    return {
        "title": article.title,
        "link": article.link,
        "published": article.published.isoformat() if article.published else None,
        "source_id": article.source_id,
        "source_name": article.source_name,
        "channel": getattr(article, "channel", None) or "news",
        "story_key": getattr(article, "story_key", None) or "",
        "summary": article.summary,
        "relevance_score": article.relevance_score,
        "itm_alignment": getattr(article, "itm_alignment", None) or "weak",
        "use_cases": list(getattr(article, "use_cases", None) or []),
        "insider_type": getattr(article, "insider_type", None),
        "processed_at": article.processed_at.isoformat() if article.processed_at else None,
        "itm_hits": [hit.model_dump(mode="json") for hit in article.entities.itm_hits],
        "operator_terms": list(article.entities.operator_terms),
        "related_detections": [
            c.model_dump(mode="json")
            for c in (getattr(article.entities, "related_detections", None) or [])
        ],
        "related_preventions": [
            c.model_dump(mode="json")
            for c in (getattr(article.entities, "related_preventions", None) or [])
        ],
        "keywords_hit": list(article.entities.keywords_hit),
        "cves": list(article.entities.cves),
        "domains": list(article.entities.domains),
    }


def filter_articles(
    articles: list[ProcessedArticle],
    *,
    min_score: float = 0.0,
    since: datetime | None = None,
    itm_alignment: str = "insider",
) -> list[ProcessedArticle]:
    mode = (itm_alignment or "insider").strip().lower()
    out: list[ProcessedArticle] = []
    for article in articles:
        if article.relevance_score < min_score:
            continue
        alignment = getattr(article, "itm_alignment", None) or "weak"
        if mode not in {"", "all", "*"} and alignment != mode:
            continue
        if since is not None:
            stamp = article.published or article.processed_at
            if stamp is not None and stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            if stamp is None or stamp < since:
                continue
        out.append(article)
    return out


def write_export_package(
    *,
    out_dir: str | Path = DEFAULT_EXPORT_DIR,
    processed_path: str | Path = DEFAULT_PROCESSED_PATH,
    min_score: float = 0.0,
    since: datetime | None = None,
    itm_alignment: str = "insider",
) -> dict[str, Any]:
    """Write articles.ndjson + manifest.json under out_dir."""
    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)

    articles = filter_articles(
        JsonlProcessedStore(processed_path).load_all(),
        min_score=min_score,
        since=since,
        itm_alignment=itm_alignment,
    )
    rows = [article_to_export_row(a) for a in articles]

    ndjson_path = dest / "articles.ndjson"
    with ndjson_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    generated_at = datetime.now(UTC)
    manifest = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "article_count": len(rows),
        "min_score": min_score,
        "itm_alignment": itm_alignment,
        "since": since.isoformat() if since else None,
        "processed_path": str(processed_path),
        "files": {
            "articles": ndjson_path.name,
        },
        "note": (
            "One-way ITM-aligned OSINT export for corporate tools. "
            "insider-intel does not read corporate Graph/Teams/email/SIEM."
        ),
    }
    manifest_path = dest / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "Export package written: %d article(s) -> %s",
        len(rows),
        dest,
    )
    return manifest
