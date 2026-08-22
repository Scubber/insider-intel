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
    indiacourts: IngestionRunResult | None = None


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
    skip_indiacourts: bool = False,
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
        # Poll open dockets of verdict-true cases for outcomes (judgment,
        # dismissal, settlement, termination); changed dockets get their new
        # entries appended and re-enrich this same cycle. No-op unless
        # DOCKET_FOLLOW_MAX_PER_RUN > 0 (sparky-only via .env.spark).
        from apps.aggregator.docket_follow import run_docket_follow

        follow_result = run_docket_follow(
            store_path=raw_path,
            processed_path=processed_path,
        )
        court_result = _merge_ingestion(
            court_result, text_result, purchase_result, follow_result
        )
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
    # Scheduled social pulls are parked behind SOCIAL_INGEST_ENABLED (operator
    # decision 2026-08-16; see shared/settings.py) — without credentials they
    # only produced per-cycle error noise.
    if not skip_social and settings.social_ingest_enabled:
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
    indiacourts_result: IngestionRunResult | None = None
    # The Indian High Court dataset lane is parked behind INDIACOURTS_ENABLED
    # (operator decision 2026-08-22): the per-PDF fetch + extract + scan work
    # belongs on the Spark tenant, and the lane is $0 there.
    if not skip_indiacourts and settings.indiacourts_enabled:
        from apps.aggregator.indiacourts_pipeline import (
            run_indiacourts_extract_pending,
            run_indiacourts_history_sweep,
            run_indiacourts_ingestion,
        )

        indiacourts_result = _merge_ingestion(
            run_indiacourts_ingestion(store_path=raw_path),
            # One bounded history slice per refresh — walks back to the
            # configured floor year (2000), hub courts first.
            run_indiacourts_history_sweep(store_path=raw_path),
            # Cool-down retries for PDFs that failed extraction / await OCR.
            run_indiacourts_extract_pending(store_path=raw_path),
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
        indiacourts_result,
    )
    # Lane-health telemetry: one smoke-test row per configured source lane,
    # enumerated from the live config so new/removed sources track
    # automatically; broken lanes get a loud [LANE-BROKEN] call-out.
    from apps.aggregator.lane_health import (
        expected_lane_specs,
        log_lane_health,
        record_lane_health,
    )

    try:
        health = record_lane_health(
            combined.sources,
            expected_lane_specs(
                feeds=sources,
                include_feedly=not skip_feedly,
                include_courtlistener=not skip_courtlistener,
                include_web_keywords=not skip_web_keywords,
                include_datatheftnews=not skip_datatheftnews,
                include_social=not skip_social,
                include_publications=not skip_publications,
                include_indiacourts=not skip_indiacourts,
            ),
        )
        log_lane_health(health)
    except Exception:  # noqa: BLE001 — telemetry must never kill a finished run
        logger.exception("Lane-health recording failed")
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
        indiacourts=indiacourts_result,
    )
