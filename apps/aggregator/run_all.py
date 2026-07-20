"""Full MVP pipeline: ingest RSS (+ optional sources) → process."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from apps.aggregator.config import get_enabled_feeds, load_feeds_from_file
from apps.aggregator.courtlistener_pipeline import (
    run_courtlistener_history_sweep,
    run_courtlistener_ingestion,
    run_courtlistener_text_backfill,
)
from apps.aggregator.datatheftnews_pipeline import run_datatheftnews_ingestion
from apps.aggregator.feedly_pipeline import run_feedly_ingestion
from apps.aggregator.pipeline import DEFAULT_STORE_PATH, run_ingestion
from apps.aggregator.process_pipeline import DEFAULT_PROCESSED_PATH, run_processing
from apps.aggregator.publications_pipeline import run_publications_ingestion
from apps.aggregator.reddit_pipeline import run_reddit_ingestion
from apps.aggregator.web_keywords import run_web_keyword_ingestion
from apps.aggregator.x_pipeline import run_x_ingestion
from shared.schemas import FeedSource, IngestionRunResult, ProcessingRunResult
from shared.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class FullRunResult:
    ingestion: IngestionRunResult
    feedly: IngestionRunResult | None
    courtlistener: IngestionRunResult | None
    web_keywords: IngestionRunResult | None
    datatheftnews: IngestionRunResult | None
    social: IngestionRunResult | None
    publications: IngestionRunResult | None
    processing: ProcessingRunResult
    raw_path: str
    processed_path: str


def _merge_ingestion(*parts: IngestionRunResult | None) -> IngestionRunResult:
    active = [p for p in parts if p is not None and p.sources]
    if not active:
        empty = parts[0]
        assert empty is not None
        return empty
    started = min(p.started_at for p in active)
    finished_times = [p.finished_at for p in active if p.finished_at]
    sources = [s for p in active for s in p.sources]
    return IngestionRunResult(
        started_at=started,
        finished_at=max(finished_times) if finished_times else None,
        sources=sources,
        total_articles_saved=sum(p.total_articles_saved for p in active),
    )


def run_full_pipeline(
    *,
    feeds_file: str | Path | None = None,
    sources: list[FeedSource] | None = None,
    raw_path: str = DEFAULT_STORE_PATH,
    processed_path: str = DEFAULT_PROCESSED_PATH,
    include_raw: bool = False,
    force_process: bool = False,
    min_score: float | None = None,
    skip_feedly: bool = False,
    skip_courtlistener: bool = False,
    skip_web_keywords: bool = False,
    skip_datatheftnews: bool = False,
    skip_social: bool = False,
    skip_publications: bool = False,
) -> FullRunResult:
    """Ingest feeds and optional sources, then process new raw articles."""
    settings = get_settings()
    score = settings.process_min_score if min_score is None else min_score

    if feeds_file is not None:
        sources = load_feeds_from_file(feeds_file)
    elif sources is None:
        sources = get_enabled_feeds()

    logger.info("Starting full pipeline: ingest → process")
    ingestion = run_ingestion(
        sources=sources,
        store_path=raw_path,
        include_raw=include_raw,
    )
    feedly_result: IngestionRunResult | None = None
    if not skip_feedly:
        feedly_result = run_feedly_ingestion(
            store_path=raw_path,
            include_raw=include_raw,
        )
    court_result: IngestionRunResult | None = None
    if not skip_courtlistener:
        court_result = run_courtlistener_ingestion(
            store_path=raw_path,
            include_raw=include_raw,
        )
        # One historical window per run — walks back to the configured floor,
        # seeding past insider prosecutions (metadata; text arrives via the
        # backfill below over subsequent runs).
        history_result = run_courtlistener_history_sweep(store_path=raw_path)
        court_result = _merge_ingestion(court_result, history_result)
        # Pull full RECAP/opinion document text for stored cases before
        # processing, so re-scoring + LLM extraction see whole filings.
        text_result = run_courtlistener_text_backfill(
            store_path=raw_path,
            processed_path=processed_path,
        )
        # Buy missing lead documents for qualifying cases (no-op without
        # PACER credentials; budget-capped under the $30/quarter fee waiver).
        from apps.aggregator.pacer_purchase import run_pacer_purchases

        purchase_result, _plan = run_pacer_purchases(
            store_path=raw_path,
            processed_path=processed_path,
        )
        court_result = _merge_ingestion(court_result, text_result, purchase_result)
    web_result: IngestionRunResult | None = None
    if not skip_web_keywords:
        web_result = run_web_keyword_ingestion(
            store_path=raw_path,
            include_raw=include_raw,
        )
    dtn_result: IngestionRunResult | None = None
    if not skip_datatheftnews:
        dtn_result = run_datatheftnews_ingestion(
            store_path=raw_path,
            include_raw=include_raw,
        )
    social_result: IngestionRunResult | None = None
    if not skip_social:
        from apps.aggregator.ingest_state import DEFAULT_STATE_PATH, JsonIngestState

        social_result = _merge_ingestion(
            run_reddit_ingestion(store_path=raw_path, include_raw=include_raw),
            # state enables the X cadence guard (free-tier quota sizing)
            run_x_ingestion(
                store_path=raw_path,
                include_raw=include_raw,
                state=JsonIngestState(DEFAULT_STATE_PATH),
            ),
        )
    publications_result: IngestionRunResult | None = None
    if not skip_publications:
        publications_result = run_publications_ingestion(
            store_path=raw_path,
            processed_path=processed_path,
            include_raw=include_raw,
        )
    processing = run_processing(
        raw_path=raw_path,
        processed_path=processed_path,
        force=force_process,
        min_score=score,
    )
    combined = _merge_ingestion(
        ingestion,
        feedly_result,
        court_result,
        web_result,
        dtn_result,
        social_result,
        publications_result,
    )
    logger.info(
        "Full pipeline done: ingested_saved=%d processed_saved=%d",
        combined.total_articles_saved,
        processing.articles_saved,
    )
    return FullRunResult(
        ingestion=combined,
        feedly=feedly_result,
        courtlistener=court_result,
        web_keywords=web_result,
        datatheftnews=dtn_result,
        social=social_result,
        publications=publications_result,
        processing=processing,
        raw_path=raw_path,
        processed_path=processed_path,
    )
