"""Processing pipeline: load raw JSONL → LangGraph agent → processed JSONL."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from apps.aggregator.pipeline import DEFAULT_STORE_PATH
from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.aggregator.storage import JsonlArticleStore
from shared.agents import process_article
from shared.schemas import ProcessingRunResult, RawArticle

logger = logging.getLogger(__name__)

DEFAULT_PROCESSED_PATH = "data/processed/articles.jsonl"


def run_processing(
    *,
    raw_path: str | Path = DEFAULT_STORE_PATH,
    processed_path: str | Path = DEFAULT_PROCESSED_PATH,
    force: bool = False,
    min_score: float = 0.0,
) -> ProcessingRunResult:
    """Process all raw articles not already in the processed store.

    Args:
        raw_path: Path to raw articles JSONL.
        processed_path: Path for processed articles JSONL.
        force: Re-process even if link already exists in processed store.
        min_score: Skip saving articles below this relevance score.
    """
    started_at = datetime.now(UTC)
    result = ProcessingRunResult(started_at=started_at)

    raw_store = JsonlArticleStore(raw_path)
    processed_store = JsonlProcessedStore(processed_path)
    raw_articles = raw_store.load_all()
    result.articles_read = len(raw_articles)

    if not raw_articles:
        logger.warning("No raw articles found at %s", raw_path)
        result.finished_at = datetime.now(UTC)
        return result

    batch: list = []
    for raw in raw_articles:
        if not force and processed_store.has_link(raw.link):
            result.articles_skipped += 1
            continue

        try:
            processed = process_article(raw)
            result.articles_processed += 1
            if processed.relevance_score < min_score:
                result.articles_skipped += 1
                continue
            batch.append(processed)
        except Exception as exc:  # noqa: BLE001 — keep processing other articles
            msg = f"{raw.link}: {exc}"
            logger.error("Failed processing article: %s", msg)
            result.errors.append(msg)

    if force and batch:
        result.articles_saved = processed_store.upsert(batch)
    else:
        result.articles_saved = processed_store.save(batch)
    result.finished_at = datetime.now(UTC)
    logger.info(
        "Processing complete: read=%d processed=%d saved=%d skipped=%d errors=%d",
        result.articles_read,
        result.articles_processed,
        result.articles_saved,
        result.articles_skipped,
        len(result.errors),
    )
    return result


def process_raw_article(raw: RawArticle):
    """Public helper for single-article processing (tests / agents)."""
    return process_article(raw)
