"""Processing pipeline: load raw JSONL → LangGraph agent → processed JSONL."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from apps.aggregator.pipeline import DEFAULT_STORE_PATH
from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.aggregator.storage import JsonlArticleStore
from shared.agents import process_article
from shared.agents.summarize import (
    SummaryBudget,
    article_qualifies,
    enrich_fields,
    merge_llm_hits,
)
from shared.llm import get_summarizer_provider
from shared.schemas import ProcessedArticle, ProcessingRunResult, RawArticle
from shared.settings import get_settings

logger = logging.getLogger(__name__)

DEFAULT_PROCESSED_PATH = "data/processed/articles.jsonl"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def run_processing(
    *,
    raw_path: str | Path = DEFAULT_STORE_PATH,
    processed_path: str | Path = DEFAULT_PROCESSED_PATH,
    force: bool = False,
    min_score: float = 0.0,
) -> ProcessingRunResult:
    """Process raw articles that are new or refreshed since last processing.

    A raw article is re-processed (and its processed row replaced) when its
    ``ingested_at`` is newer than the stored ``processed_at`` — this picks up
    rows rewritten by the ingest store's refresh path. Known quirk: if a
    refreshed article now scores below ``min_score``, its stale processed row
    is left in place.

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

    # Collapse legacy duplicate lines (latest wins, order preserved).
    by_link: dict[str, RawArticle] = {}
    for raw in raw_articles:
        by_link[raw.link] = raw

    prior_by_link: dict[str, ProcessedArticle] = {a.link: a for a in processed_store.load_all()}

    settings = get_settings()
    budget = SummaryBudget(settings.summarizer_max_articles_per_run)

    batch: list = []
    reprocessed_existing = False
    for raw in by_link.values():
        prior = prior_by_link.get(raw.link)
        if (
            not force
            and prior is not None
            and _as_utc(prior.processed_at) >= _as_utc(raw.ingested_at)
        ):
            result.articles_skipped += 1
            continue

        try:
            processed = process_article(raw, prior=prior, budget=budget)
            result.articles_processed += 1
            if processed.relevance_score < min_score:
                result.articles_skipped += 1
                continue
            if raw.link in prior_by_link:
                reprocessed_existing = True
            batch.append(processed)
        except Exception as exc:  # noqa: BLE001 — keep processing other articles
            msg = f"{raw.link}: {exc}"
            logger.error("Failed processing article: %s", msg)
            result.errors.append(msg)

    if (force or reprocessed_existing) and batch:
        result.articles_saved = processed_store.upsert(batch)
    else:
        result.articles_saved = processed_store.save(batch)

    _backfill_summaries(
        processed_store,
        budget=budget,
        settings=settings,
        exclude_links={a.link for a in batch},
    )
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


def _backfill_summaries(
    processed_store: JsonlProcessedStore,
    *,
    budget: SummaryBudget,
    settings,
    exclude_links: set[str],
) -> int:
    """Enrich stored qualifying articles that lack a forensic record.

    Already-processed rows are skipped forever by the main loop, so without
    this sweep the existing corpus would never gain forensic records. Two
    tiers, each newest-published first: never-enriched rows before legacy
    upgrades (a fixed per-run budget always spends on new coverage first).
    Bounded by the shared budget, so the corpus converts over successive 6h
    refreshes at a fixed cost ceiling.
    """
    if get_summarizer_provider(settings) is None or budget.remaining <= 0:
        return 0

    upgrade_legacy = settings.summarizer_upgrade_legacy
    fresh: list[ProcessedArticle] = []
    legacy: list[ProcessedArticle] = []
    filing_min_chars = settings.summarizer_filing_min_text_chars
    for row in processed_store.load_all():
        if (
            row.link in exclude_links
            or row.forensics is not None
            or not article_qualifies(row, filing_min_chars=filing_min_chars)
        ):
            continue
        if row.case_record is None:
            fresh.append(row)
        elif upgrade_legacy:
            legacy.append(row)
    if not fresh and not legacy:
        return 0
    order = lambda a: _as_utc(a.published or a.processed_at)  # noqa: E731
    fresh.sort(key=order, reverse=True)
    legacy.sort(key=order, reverse=True)
    candidates = fresh + legacy

    updated: list[ProcessedArticle] = []
    for row in candidates:
        if budget.remaining <= 0:
            break
        summary, forensics, record, llm_hits = enrich_fields(
            title=row.title,
            source=row.source_id,
            text=row.clean_text or row.summary or "",
            lexical_hits=row.entities.itm_hits,
            use_cases=row.use_cases,
            settings=settings,
            budget=budget,
        )
        if forensics is None:
            logger.warning("Backfill enrichment failed for %s", row.link)
            continue
        # Drop any stale LLM-adjudicated hits from the old run before re-merging,
        # then stamp the final catalog-validated ids onto the record.
        lexical_entities = row.entities.model_copy(
            update={
                "itm_hits": [
                    h for h in row.entities.itm_hits if getattr(h, "source", "lexical") != "llm"
                ]
            }
        )
        merged = merge_llm_hits(lexical_entities, llm_hits)
        forensics = forensics.model_copy(
            update={
                "link": row.link,
                "title": row.title,
                "candidate_technique_ids": [h.id.upper() for h in merged.itm_hits],
            }
        )
        updated.append(
            row.model_copy(
                update={
                    "ai_summary": summary,
                    "case_record": record,
                    "forensics": forensics,
                    "entities": merged,
                }
            )
        )

    if updated:
        processed_store.upsert(updated)
        logger.info("Backfilled forensic records for %d article(s)", len(updated))
    return len(updated)


def process_raw_article(raw: RawArticle):
    """Public helper for single-article processing (tests / agents)."""
    return process_article(raw)
