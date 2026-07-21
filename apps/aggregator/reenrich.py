"""Re-enrich only the cases missed by an earlier enricher configuration.

A "missed" case is a **filings-channel** row whose stored forensic record was
produced by a model other than the current target (e.g. the pre-Sonnet-5 /
pre-12k-cap Haiku enricher, whose rich filings truncated). Clearing its
paid-for LLM fields drops it back into the budget-bounded backfill sweep, which
re-enriches it on the current model. This never touches news/social/publications
and never re-bills a row already on the target model — so it is safe to leave
enabled: once every filing is on the target model, it converges to a no-op.

Scoped to filings because that is where the truncation and the model-quality
gap matter; broadening to other channels would be a separate, larger re-bill.
"""

from __future__ import annotations

import logging
from pathlib import Path

from apps.aggregator.processed_storage import JsonlProcessedStore
from shared.schemas import ProcessedArticle
from shared.schemas.articles import resolve_channel
from shared.schemas.forensics import ENRICH_SCHEMA_VERSION

logger = logging.getLogger(__name__)


def _is_filing(row: ProcessedArticle) -> bool:
    return resolve_channel(row.source_id, getattr(row, "channel", None)) == "filings"


def _forensics_model(row: ProcessedArticle) -> str:
    forensics = getattr(row, "forensics", None)
    if forensics is None:
        return ""
    return (getattr(forensics, "model", None) or "").strip()


def _schema_version(row: ProcessedArticle) -> int:
    forensics = getattr(row, "forensics", None)
    if forensics is None:
        return 0
    return int(getattr(forensics, "schema_version", 1) or 1)


def select_missed_filings(
    processed_path: str | Path,
    *,
    target_model: str,
    limit: int | None = None,
) -> list[str]:
    """Links of enriched filings that are stale — wrong model or an old schema.

    A filing is "missed" when its stored forensics came from a non-target model
    *or* was written under an older clamp generation (schema_version <
    ENRICH_SCHEMA_VERSION, e.g. the tight pre-safety-bound clamps that truncated
    method/narrative text). Never-enriched rows (no forensics) are excluded — the
    normal backfill sweep already picks those up. Ordered newest-filed first so a
    capped run recovers the freshest cases first (mirrors the sweep's ordering).
    """
    from apps.aggregator.process_pipeline import _as_utc

    target = (target_model or "").strip()
    store = JsonlProcessedStore(processed_path)
    missed: list[ProcessedArticle] = []
    for row in store.load_all():
        if not _is_filing(row):
            continue
        if getattr(row, "forensics", None) is None:
            continue  # never enriched → the normal sweep handles it
        on_target_model = bool(target) and _forensics_model(row) == target
        on_current_schema = _schema_version(row) >= ENRICH_SCHEMA_VERSION
        if on_target_model and on_current_schema:
            continue  # already on the target model AND the current clamp schema
        missed.append(row)

    missed.sort(key=lambda r: _as_utc(r.published or r.processed_at), reverse=True)
    links = [r.link for r in missed]
    if limit is not None and limit >= 0:
        links = links[:limit]
    return links


def clear_missed_filings(
    processed_path: str | Path,
    *,
    target_model: str,
    limit: int | None = None,
) -> int:
    """Clear paid-for LLM fields on missed filings so the sweep re-enriches them.

    Returns the number of rows cleared. No LLM spend here — the re-enrichment
    happens in the subsequent budget-bounded backfill sweep.
    """
    from apps.aggregator.courtlistener_pipeline import _clear_llm_fields

    links = select_missed_filings(processed_path, target_model=target_model, limit=limit)
    if links:
        _clear_llm_fields(str(processed_path), set(links))
        logger.info(
            "Re-enrich missed: cleared %d filing(s) not on target model %r",
            len(links),
            target_model,
        )
    return len(links)
