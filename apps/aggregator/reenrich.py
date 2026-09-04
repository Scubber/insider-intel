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

Non-destructiveness is now guaranteed structurally by
``ProcessedArticle.enrichment_history``: every generation is archived and the
enrich node select-bests over it, so a thin re-enrichment can never gut a
richer prior record. Clearing a filing (below) drops only the *selected*
pointer, never the history. The snapshot/reconcile pair here is retained as
secondary belt-and-suspenders and, given select-best, converges to restoring
nothing.
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


def _richness(row: ProcessedArticle | None) -> float:
    """Enrichment richness — higher means more analyst value.

    Mirrors the recovery merge's scoring: an analyst note dominates, then the
    method count, then forensic confidence. A gutted re-enrichment (no note,
    methods=0) scores ~0; a full record scores well above it.
    """
    if row is None:
        return 0.0
    ai = 100.0 if (getattr(row, "ai_summary", None) or "").strip() else 0.0
    forensics = getattr(row, "forensics", None)
    methods = len(forensics.methods) if forensics is not None and forensics.methods else 0
    try:
        conf = float(getattr(forensics, "confidence", 0.0) or 0.0) if forensics else 0.0
    except (TypeError, ValueError):
        conf = 0.0
    # Mirrors enrichment_richness: an adjudicated outcome outranks a
    # one-method deficit (docket-follow re-enrichments must not lose to the
    # complaint-stage record for knowing less procedure but more result).
    outcome = 15.0 if (getattr(forensics, "outcome", None) or "").strip() else 0.0
    return ai + methods * 10.0 + conf + outcome


def snapshot_and_clear_missed_filings(
    processed_path: str | Path,
    *,
    target_model: str,
    limit: int | None = None,
) -> tuple[dict[str, ProcessedArticle], int]:
    """Snapshot missed filings' current records, then clear them for the sweep.

    Returns ``(snapshot, count)`` where ``snapshot`` maps link -> the pre-clear
    ``ProcessedArticle``. Pair with :func:`reconcile_reenriched` after the
    backfill sweep so a re-enrichment that comes back empty (a filing whose
    source text isn't rich enough — e.g. a docket with no archived document)
    restores its prior record instead of leaving the card gutted. No LLM spend
    here; the re-enrichment happens in the subsequent budget-bounded sweep.
    """
    from apps.aggregator.courtlistener_pipeline import _clear_llm_fields

    links = select_missed_filings(processed_path, target_model=target_model, limit=limit)
    if not links:
        return {}, 0
    link_set = set(links)
    store = JsonlProcessedStore(processed_path)
    snapshot = {row.link: row for row in store.load_all() if row.link in link_set}
    _clear_llm_fields(str(processed_path), link_set)
    logger.info(
        "Re-enrich missed: snapshotted + cleared %d filing(s) not on target model %r",
        len(links),
        target_model,
    )
    return snapshot, len(links)


def reconcile_reenriched(
    processed_path: str | Path,
    snapshot: dict[str, ProcessedArticle],
) -> int:
    """Keep-best after re-enrichment: restore any record the sweep regressed.

    For each snapshotted filing, compare the post-sweep record against the
    pre-clear one. Keep the new record whenever it is at least as rich (so the
    widened clamps / current model apply), but restore the prior record when the
    re-enrichment came back strictly poorer — i.e. it re-enriched to an empty or
    floor result over source text too thin to ground a record. This makes the
    whole re-enrich non-destructive: rich is never overwritten by empty.

    Returns the number of rows restored.
    """
    if not snapshot:
        return 0
    store = JsonlProcessedStore(processed_path)
    current = {row.link: row for row in store.load_all() if row.link in snapshot}
    restore: list[ProcessedArticle] = []
    for link, old in snapshot.items():
        new = current.get(link)
        if new is None:
            continue
        if _richness(old) > _richness(new):
            restore.append(old)
    if restore:
        store.upsert(restore)
        logger.info(
            "Re-enrich reconcile: restored %d filing(s) whose re-enrichment regressed",
            len(restore),
        )
    return len(restore)


def clear_missed_filings(
    processed_path: str | Path,
    *,
    target_model: str,
    limit: int | None = None,
) -> int:
    """Clear the selected LLM fields on missed filings so the sweep re-enriches.

    Clears only the *selected* pointer (ai_summary/case_record/forensics);
    ``enrichment_history`` is preserved, so the subsequent re-enrich re-selects
    the richest generation and an empty re-run cannot gut the row. The
    snapshot/reconcile pair adds nothing beyond that now, but remains available.
    Used by the CLI dry-run/count path. Returns the number of rows cleared.
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


# --- Additive-field backfill (docs/schema-freeze-v4.md) -----------------------
#
# An ADDITIVE field lands at the current schema tier without a bump, so the
# reenrich lane above (which keys on model/tier) never re-selects rows whose
# stored record simply predates the field. This lane targets those rows by
# the field's own absence — ANY channel, not filings-only — and reuses the
# same _clear_llm_fields path (history preserved) so the nightly sweep
# re-enriches them on the next cycle; the overlay rule then fills the value
# from the new generation while select-best keeps whichever generation wins.

DEFAULT_FIELD_BACKFILL_INDUSTRIES: tuple[str, ...] = ("financial-services", "unknown")


def _row_channel(row: ProcessedArticle) -> str:
    return resolve_channel(row.source_id, getattr(row, "channel", None)) or "unknown"


def select_field_backfill_targets(
    processed_path: str | Path,
    *,
    field: str,
    industries: tuple[str, ...] | list[str] | None = DEFAULT_FIELD_BACKFILL_INDUSTRIES,
    limit: int | None = None,
) -> list[ProcessedArticle]:
    """Rows whose stored projection lacks an additive ``field`` and should re-enrich.

    Gates: verdict-true (``is_insider_case``), current schema tier
    (``schema_version >= ENRICH_SCHEMA_VERSION``), ``forensics.<field>`` is
    None, and the victim ``industry`` is in ``industries`` (None = any). ANY
    channel qualifies — unlike :func:`select_missed_filings`. Newest first so a
    capped run covers the freshest cases; ``limit`` truncates.
    """
    from apps.aggregator.process_pipeline import _as_utc

    wanted = set(industries) if industries is not None else None
    store = JsonlProcessedStore(processed_path)
    targets: list[ProcessedArticle] = []
    for row in store.load_all():
        forensics = getattr(row, "forensics", None)
        if forensics is None:
            continue  # never enriched → the normal sweep handles it
        if not bool(getattr(forensics, "is_insider_case", False)):
            continue
        if _schema_version(row) < ENRICH_SCHEMA_VERSION:
            continue  # the reenrich lane owns stale tiers
        if getattr(forensics, field, None) is not None:
            continue
        if wanted is not None and (getattr(forensics, "industry", None) or "unknown") not in wanted:
            continue
        targets.append(row)
    targets.sort(key=lambda r: _as_utc(r.published or r.processed_at), reverse=True)
    if limit is not None and limit >= 0:
        targets = targets[:limit]
    return targets


def summarize_field_backfill_targets(rows: list[ProcessedArticle]) -> dict[str, dict[str, int]]:
    """Counts by channel and by victim industry — never links or titles.

    The dry-run output can land in CI logs (sparky-ops), so it must carry
    nothing that identifies a case.
    """
    by_channel: dict[str, int] = {}
    by_industry: dict[str, int] = {}
    for row in rows:
        ch = _row_channel(row)
        by_channel[ch] = by_channel.get(ch, 0) + 1
        ind = getattr(row.forensics, "industry", None) or "unknown"
        by_industry[ind] = by_industry.get(ind, 0) + 1
    return {"total": {"rows": len(rows)}, "by_channel": by_channel, "by_industry": by_industry}


def clear_field_backfill_targets(
    processed_path: str | Path,
    *,
    field: str,
    industries: tuple[str, ...] | list[str] | None = DEFAULT_FIELD_BACKFILL_INDUSTRIES,
    limit: int | None = None,
) -> int:
    """Clear the selected LLM view on the targets so the next sweep re-enriches.

    Reuses ``_clear_llm_fields`` — only the projection pointer is cleared;
    ``enrichment_history`` is preserved, so the re-enrichment appends a new
    generation and select-best (plus the additive overlay) re-projects. Returns
    the number of rows cleared.
    """
    from apps.aggregator.courtlistener_pipeline import _clear_llm_fields

    rows = select_field_backfill_targets(
        processed_path, field=field, industries=industries, limit=limit
    )
    if not rows:
        return 0
    _clear_llm_fields(str(processed_path), {r.link for r in rows})
    logger.info("Field backfill: cleared %d row(s) lacking forensics.%s", len(rows), field)
    return len(rows)
