"""Processing pipeline: load raw JSONL → LangGraph agent → processed JSONL."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from apps.aggregator.pipeline import DEFAULT_STORE_PATH
from apps.aggregator.processed_storage import JsonlProcessedStore
from apps.aggregator.storage import JsonlArticleStore
from apps.aggregator.technique_seeds import TechniqueSeedStore, rebuild_technique_seeds
from shared.agents import process_article
from shared.agents.discover import discover_case
from shared.agents.summarize import (
    SummaryBudget,
    article_qualifies,
    enrich_fields,
    merge_llm_hits,
)
from shared.llm import get_discoverer_provider, get_summarizer_provider
from shared.schemas import ProcessedArticle, ProcessingRunResult, RawArticle
from shared.schemas.articles import resolve_channel
from shared.settings import get_settings
from shared.utils.story_key import compute_story_key

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
    # The fresh-ingest batch may not spend the whole budget: a reserved slice
    # is guaranteed to the backfill sweep (filings-first), or a heavy news day
    # starves the stored court-case backlog forever.
    cap = settings.summarizer_max_articles_per_run
    reserve = min(settings.summarizer_backfill_reserve, cap)
    budget = SummaryBudget(cap - reserve)
    discover_budget = SummaryBudget(settings.discoverer_max_articles_per_run)

    # Fresh-ingest syndication dedupe: outlets that publish the identical story
    # under several domains (same title+day → same story_key) get one enrichment,
    # not one per link — siblings are processed with a zero LLM budget and still
    # land as floor rows. Filings are exempt: their keys are case-number based
    # and dockets don't syndicate.
    zero_budget = SummaryBudget(0)
    enriched_story_keys: set[str] = {
        a.story_key
        for a in prior_by_link.values()
        if a.forensics is not None and (a.story_key or "").strip()
    }

    # Enrich the fresh-ingest batch newest-first (filings first, then newest
    # published) so the limited per-run LLM budget goes to genuinely-new cases
    # before force-refreshed historical filings. The CourtListener text backfill
    # re-stamps old dockets with a fresh ingested_at, which pulls them back into
    # this batch; without an ordering key they'd compete in raw-file order and a
    # decade-seeding wave could starve the day's new case. `published` (the
    # filing date) sorts a 2016 docket behind a 2026 one. Mirrors the backfill
    # sweep's own ordering.
    def _fresh_order(raw: RawArticle):
        is_filing = resolve_channel(raw.source_id, raw.channel) == "filings"
        return (is_filing, _as_utc(raw.published or raw.ingested_at))

    batch: list = []
    reprocessed_existing = False
    for raw in sorted(by_link.values(), key=_fresh_order, reverse=True):
        prior = prior_by_link.get(raw.link)
        if (
            not force
            and prior is not None
            and _as_utc(prior.processed_at) >= _as_utc(raw.ingested_at)
        ):
            result.articles_skipped += 1
            continue

        is_filing = resolve_channel(raw.source_id, raw.channel) == "filings"
        raw_key = (
            ""
            if is_filing
            else compute_story_key(raw.title, raw.published, fallback=raw.ingested_at)
        )
        sibling_enriched = bool(raw_key) and raw_key in enriched_story_keys
        try:
            processed = process_article(
                raw,
                prior=prior,
                budget=zero_budget if sibling_enriched else budget,
                discover_budget=zero_budget if sibling_enriched else discover_budget,
            )
            if processed.forensics is not None and (processed.story_key or "").strip():
                enriched_story_keys.add(processed.story_key)
            result.articles_processed += 1
            # Curated publications bypass the score gate: long reference docs
            # dilute keyword density and would otherwise silently drop out.
            is_publication = resolve_channel(raw.source_id, raw.channel) == "publications"
            if not is_publication and processed.relevance_score < min_score:
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

    batch_links = {a.link for a in batch}

    # One-off recovery: clear the paid-for LLM fields on "missed" filings (a
    # forensic record from a model other than the target) so the sweep below
    # re-enriches them on the current model. Env-gated (0 = off); idempotent —
    # converges to a no-op once every filing is on the target model.
    if settings.summarizer_reenrich_missed_limit > 0:
        from apps.aggregator.reenrich import clear_missed_filings

        target_model = (
            settings.summarizer_reenrich_model
            or settings.summarizer_model
            or settings.anthropic_model
        )
        cleared = clear_missed_filings(
            processed_path,
            target_model=target_model,
            limit=settings.summarizer_reenrich_missed_limit,
        )
        result.reenrich_cleared = cleared

    # Backfill allowance = the reserved slice plus whatever the batch left over.
    backfill_budget = SummaryBudget(reserve + budget.remaining)
    _backfill_summaries(
        processed_store,
        budget=backfill_budget,
        settings=settings,
        exclude_links=batch_links,
    )
    _backfill_discovery(
        processed_store,
        budget=discover_budget,
        settings=settings,
        exclude_links=batch_links,
    )
    # Recompute the novel-candidate view from the whole corpus (cheap, no LLM).
    # Best-effort: it's a derived cache, so a non-writable state dir degrades to
    # a stale view rather than sinking the whole ingest run.
    try:
        rebuild_technique_seeds(
            processed_store,
            store=TechniqueSeedStore(settings.technique_seeds_path),
            generated_at=datetime.now(UTC),
        )
    except OSError as exc:
        logger.warning("Could not write technique-seeds view: %s", exc)
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
    tiers, each filings-first then newest-published: never-enriched rows
    before legacy upgrades (a fixed per-run budget always spends on new
    coverage first, and court filings outrank the daily news churn).
    Bounded by the shared budget, so the corpus converts over successive 6h
    refreshes at a fixed cost ceiling.
    """
    if get_summarizer_provider(settings) is None or budget.remaining <= 0:
        return 0

    upgrade_legacy = settings.summarizer_upgrade_legacy
    fresh: list[ProcessedArticle] = []
    legacy: list[ProcessedArticle] = []
    filing_min_chars = settings.summarizer_filing_min_text_chars
    # Syndication dedupe: one enrichment per story. ISMG-style outlets publish
    # the identical article under several domains (distinct links, same
    # story_key) — billing each sibling separately is pure waste, and the
    # cluster card only surfaces one member anyway (pick_primary prefers the
    # enriched one).
    enriched_keys: set[str] = set()
    for row in processed_store.load_all():
        if row.forensics is not None and (row.story_key or "").strip():
            enriched_keys.add(row.story_key)
    for row in processed_store.load_all():
        if (
            row.link in exclude_links
            or row.forensics is not None
            or (row.story_key or "").strip() in enriched_keys
            or not article_qualifies(row, filing_min_chars=filing_min_chars)
        ):
            continue
        if row.case_record is None:
            fresh.append(row)
        elif upgrade_legacy:
            legacy.append(row)
    if not fresh and not legacy:
        return 0

    # Court filings first, then newest: a filing's `published` is its filing
    # date (often historical), so pure recency starves the court cases — the
    # richest forensic sources — behind every fresh news day.
    def order(a: ProcessedArticle):
        is_filing = resolve_channel(a.source_id, getattr(a, "channel", None)) == "filings"
        return (is_filing, _as_utc(a.published or a.processed_at))

    fresh.sort(key=order, reverse=True)
    legacy.sort(key=order, reverse=True)
    candidates = fresh + legacy

    # Flush every few conversions: enrichment is paid-for work, and a task
    # timeout (Cloud Run kills + retries the whole run) must never discard a
    # swept batch — losing it re-bills the same articles on the retry.
    checkpoint_every = 5
    total_saved = 0
    updated: list[ProcessedArticle] = []
    for row in candidates:
        if budget.remaining <= 0:
            break
        # Same-run syndication dedupe: the sort put the best representative of
        # each story first; its siblings are skipped once it enriches.
        row_key = (row.story_key or "").strip()
        if row_key and row_key in enriched_keys:
            continue
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
        if row_key:
            enriched_keys.add(row_key)
        if len(updated) >= checkpoint_every:
            processed_store.upsert(updated)
            total_saved += len(updated)
            updated = []
            logger.info(
                "Backfill checkpoint: %d saved this run, %d budget remaining",
                total_saved,
                budget.remaining,
            )

    if updated:
        processed_store.upsert(updated)
        total_saved += len(updated)
    if total_saved:
        logger.info("Backfilled forensic records for %d article(s)", total_saved)
    return total_saved


def _backfill_discovery(
    processed_store: JsonlProcessedStore,
    *,
    budget: SummaryBudget,
    settings,
    exclude_links: set[str],
) -> int:
    """Run the discovery pass on enriched rows that lack a discovery record.

    Mirrors ``_backfill_summaries``: budget-bounded, newest-published-first, so
    the corpus converts over successive refreshes. Only rows that already have a
    forensic record (an insider case with methods) but no discovery are
    candidates — discovery consumes the vetted extraction, never raw text.
    """
    if get_discoverer_provider(settings) is None or budget.remaining <= 0:
        return 0

    # One discovery per story: syndicated siblings share a story_key, and the
    # seed store counts corroboration by distinct story_key anyway — a second
    # sibling adds spend, not signal.
    discovered_keys: set[str] = set()
    candidates: list[ProcessedArticle] = []
    for row in processed_store.load_all():
        if row.discovery is not None and (row.story_key or "").strip():
            discovered_keys.add(row.story_key)
    for row in processed_store.load_all():
        if row.link in exclude_links or row.discovery is not None:
            continue
        forensics = row.forensics
        if forensics is None or not forensics.is_insider_case or not forensics.methods:
            continue
        candidates.append(row)
    if not candidates:
        return 0
    candidates.sort(key=lambda a: _as_utc(a.published or a.processed_at), reverse=True)

    updated: list[ProcessedArticle] = []
    for row in candidates:
        if budget.remaining <= 0:
            break
        row_key = (row.story_key or "").strip()
        if row_key and row_key in discovered_keys:
            continue
        discovery = discover_case(forensics=row.forensics, settings=settings, budget=budget)
        if discovery is None:
            continue
        updated.append(row.model_copy(update={"discovery": discovery}))
        if row_key:
            discovered_keys.add(row_key)

    if updated:
        processed_store.upsert(updated)
        logger.info("Backfilled discovery for %d article(s)", len(updated))
    return len(updated)


def process_raw_article(raw: RawArticle):
    """Public helper for single-article processing (tests / agents)."""
    return process_article(raw)
