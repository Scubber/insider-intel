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

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

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
# the field's own absence — ANY channel, not filings-only — and queues them in
# a state/ file the sweep consumes: the row is RE-ENRICHED WITHOUT CLEARING
# (its projection stays live on EVIDENCE/TOOLING/the stream throughout), the
# new generation appends to history, and project_from_history re-projects —
# select-best keeps whichever generation wins, the overlay fills the field.
# Clearing (the reenrich lane's mechanism) is deliberately NOT used here: it
# nulls forensics for the whole backfill window and strands any row the sweep
# then refuses (gate/sibling), with no projection at all.

DEFAULT_FIELD_BACKFILL_INDUSTRIES: tuple[str, ...] = ("financial-services", "unknown")


def _row_channel(row: ProcessedArticle) -> str:
    return resolve_channel(row.source_id, getattr(row, "channel", None)) or "unknown"


class FieldBackfillSelection(NamedTuple):
    """Targets that the sweep WILL enrich vs rows it would refuse."""

    queued: list[ProcessedArticle]
    skipped_by_gate: list[ProcessedArticle]


def select_field_backfill_targets(
    processed_path: str | Path,
    *,
    field: str,
    industries: tuple[str, ...] | list[str] | None = DEFAULT_FIELD_BACKFILL_INDUSTRIES,
    limit: int | None = None,
    filing_min_chars: int = 1_500,
) -> FieldBackfillSelection:
    """Rows whose stored projection lacks an additive ``field``.

    Gates: verdict-true (``is_insider_case``), current schema tier
    (``schema_version >= ENRICH_SCHEMA_VERSION``), ``forensics.<field>`` is
    None, and the victim ``industry`` is in ``industries`` (None = any). ANY
    channel qualifies — unlike :func:`select_missed_filings`. Rows that fail
    ``article_qualifies`` (the spend policy the sweep applies) land in
    ``skipped_by_gate`` instead of being queued — nothing is queued that the
    sweep would refuse. Newest first; ``limit`` truncates the queued list.
    """
    from apps.aggregator.process_pipeline import _as_utc
    from shared.agents.summarize import article_qualifies

    wanted = set(industries) if industries is not None else None
    store = JsonlProcessedStore(processed_path)
    queued: list[ProcessedArticle] = []
    skipped: list[ProcessedArticle] = []
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
        if article_qualifies(row, filing_min_chars=filing_min_chars, use_itm_alignment=True):
            queued.append(row)
        else:
            skipped.append(row)
    order = lambda r: _as_utc(r.published or r.processed_at)  # noqa: E731
    queued.sort(key=order, reverse=True)
    skipped.sort(key=order, reverse=True)
    if limit is not None and limit >= 0:
        queued = queued[:limit]
    return FieldBackfillSelection(queued=queued, skipped_by_gate=skipped)


def _count_by(rows: list[ProcessedArticle]) -> dict[str, dict[str, int]]:
    by_channel: dict[str, int] = {}
    by_industry: dict[str, int] = {}
    for row in rows:
        ch = _row_channel(row)
        by_channel[ch] = by_channel.get(ch, 0) + 1
        ind = getattr(row.forensics, "industry", None) or "unknown"
        by_industry[ind] = by_industry.get(ind, 0) + 1
    return {"rows": {"total": len(rows)}, "by_channel": by_channel, "by_industry": by_industry}


def summarize_field_backfill_targets(sel: FieldBackfillSelection) -> dict[str, dict]:
    """Counts by channel and by victim industry — never links or titles.

    The dry-run output can land in CI logs (sparky-ops), so it must carry
    nothing that identifies a case.
    """
    return {"queued": _count_by(sel.queued), "skipped_by_gate": _count_by(sel.skipped_by_gate)}


# --- the queue: data/state/field_backfill_targets.json (settings-resolved) ----
#
# Shape: {"field": str, "links": [str, ...], "written_at": iso}. The CLI writes
# it; the sweep consumes it and rewrites it minus the links whose generation
# landed — a per-cycle state/ file, never checked in (CLAUDE.md dynamic-data
# rule). A link stays queued until a generation actually lands for it.


def load_field_backfill_queue(path: str | Path) -> dict:
    file_path = Path(path)
    if not file_path.exists():
        return {"field": "", "links": []}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Unreadable field-backfill queue at %s — treating as empty", file_path)
        return {"field": "", "links": []}
    links = [str(link) for link in (data.get("links") or []) if link]
    return {"field": str(data.get("field") or ""), "links": links}


def write_field_backfill_queue(path: str | Path, *, field: str, links: list[str]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "field": field,
        "links": list(dict.fromkeys(links)),
        "written_at": datetime.now(UTC).isoformat(),
    }
    tmp = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(file_path)


def queue_field_backfill_targets(
    processed_path: str | Path,
    *,
    field: str,
    queue_path: str | Path,
    industries: tuple[str, ...] | list[str] | None = DEFAULT_FIELD_BACKFILL_INDUSTRIES,
    limit: int | None = None,
    filing_min_chars: int = 1_500,
) -> int:
    """Write the selected targets to the queue file (union with what's pending).

    No LLM spend and NO clearing — the rows keep their projection. The next
    sweep re-enriches queued links first-come within the reserve and drops
    each from the file once its generation lands. Returns the queue length.
    """
    sel = select_field_backfill_targets(
        processed_path,
        field=field,
        industries=industries,
        limit=limit,
        filing_min_chars=filing_min_chars,
    )
    pending = load_field_backfill_queue(queue_path)
    carried = pending["links"] if pending["field"] == field else []
    links = [*carried, *(r.link for r in sel.queued)]
    write_field_backfill_queue(queue_path, field=field, links=links)
    logger.info(
        "Field backfill: queued %d row(s) lacking forensics.%s (%d carried, %d gate-skipped)",
        len(links),
        field,
        len(carried),
        len(sel.skipped_by_gate),
    )
    return len(links)
