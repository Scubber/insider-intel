"""Ingest enricher: one LLM call → ai_summary + forensic record + ITM adjudication.

Shared by the article-processor graph node and the pipeline backfill sweep.
Every failure path degrades to "no enrichment" — a missing record is never an
error, and the heuristics-only pipeline behaves exactly as before when
SUMMARIZER_LLM_PROVIDER is unset. The legacy ``CaseRecord`` is derived from the
forensic record so the existing analyst-note UI keeps working unchanged.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shared.itm.controls import resolve_controls
from shared.llm import ItmRef, get_summarizer_provider
from shared.schemas.articles import CaseRecord, ExtractedEntities, ItmHit, resolve_channel
from shared.schemas.forensics import (
    PerCaseForensics,
    case_record_from_forensics,
    parse_forensics_json,
)
from shared.utils.embeddings import cosine_similarity, get_default_embedder

if TYPE_CHECKING:
    from shared.settings import Settings

logger = logging.getLogger(__name__)

# LLM-proposed techniques below this self-reported confidence are dropped.
MIN_ITM_REF_CONFIDENCE = 0.6
# Cap LLM-added hits per article so one chatty reply can't spam the matrix.
MAX_LLM_ITM_HITS = 5
# Candidate shortlist size offered to the LLM for adjudication.
SHORTLIST_SIZE = 20
_CANDIDATE_DESC_CHARS = 150


class SummaryBudget:
    """Per-run LLM-call allowance shared across node calls and backfill."""

    def __init__(self, limit: int) -> None:
        self.limit = max(0, int(limit))
        self.spent = 0

    def take(self) -> bool:
        if self.spent >= self.limit:
            return False
        self.spent += 1
        return True

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.spent)


def qualifies(
    *,
    itm_hits: list,
    use_cases: list[str],
    channel: str = "",
    text: str = "",
    filing_min_chars: int = 1_500,
) -> bool:
    """Spend LLM calls only where the article shows some insider signal.

    A lexical ITM hit or a matched use-case always qualifies. Court filings
    are additionally pre-filtered as insider-relevant by the CourtListener
    ingestion query, so once their full document body is present — ``text``
    (clean_text) at or above ``filing_min_chars``, not just a docket-entry
    stub — they qualify even without a lexical hit. That is exactly the card
    where an analyst summary matters and the raw docket text reads worst.
    """
    if itm_hits or use_cases:
        return True
    if channel == "filings" and len((text or "").strip()) >= max(1, filing_min_chars):
        return True
    return False


def article_qualifies(article, *, filing_min_chars: int = 1_500) -> bool:
    """`qualifies` for a ProcessedArticle-shaped object (backfill path)."""
    entities = getattr(article, "entities", None)
    hits = list(getattr(entities, "itm_hits", None) or [])
    return qualifies(
        itm_hits=hits,
        use_cases=list(getattr(article, "use_cases", None) or []),
        channel=resolve_channel(getattr(article, "source_id", "") or ""),
        text=getattr(article, "clean_text", "") or "",
        filing_min_chars=filing_min_chars,
    )


_TECH_VECTORS: list[tuple[str, list[float]]] | None = None


def _technique_vectors() -> list[tuple[str, list[float]]]:
    """Embed every catalog technique's title+description once per process."""
    global _TECH_VECTORS
    if _TECH_VECTORS is None:
        from shared.itm.index import load_itm_index

        embedder = get_default_embedder()
        _TECH_VECTORS = [
            (tech.id, embedder.embed(f"{tech.title} {tech.description_text}"))
            for tech in load_itm_index().techniques
        ]
    return _TECH_VECTORS


def clear_technique_vector_cache() -> None:
    """Test hook (paired with shared.itm.index.clear_itm_cache)."""
    global _TECH_VECTORS
    _TECH_VECTORS = None


def build_itm_candidates(
    text: str,
    lexical_hits: list[ItmHit],
    *,
    k: int = SHORTLIST_SIZE,
) -> str:
    """Shortlist of techniques for the LLM to adjudicate.

    Lexical hits are always included; the rest of the slots go to the
    nearest techniques by hashing-embedding similarity, so the LLM can
    surface behaviors the alias matcher missed.
    """
    from shared.itm.index import load_itm_index

    by_id = {tech.id: tech for tech in load_itm_index().techniques}
    if not by_id:
        return ""

    chosen: list[str] = []
    seen: set[str] = set()
    for hit in lexical_hits:
        if hit.id in by_id and hit.id not in seen:
            seen.add(hit.id)
            chosen.append(hit.id)

    article_vec = get_default_embedder().embed(text or "")
    if any(article_vec):
        scored = [
            (cosine_similarity(article_vec, vec), tech_id)
            for tech_id, vec in _technique_vectors()
            if tech_id not in seen
        ]
        scored.sort(reverse=True)
        for _, tech_id in scored[: max(0, k - len(chosen))]:
            chosen.append(tech_id)

    lines = []
    for tech_id in chosen[: max(k, len(lexical_hits))]:
        tech = by_id[tech_id]
        desc = (tech.description_text or "").strip().replace("\n", " ")
        lines.append(f"{tech.id} — {tech.title} ({tech.theme}): {desc[:_CANDIDATE_DESC_CHARS]}")
    return "\n".join(lines)


def _validate_itm_refs(refs: list, lexical_hits: list[ItmHit]) -> list[ItmHit]:
    """Catalog-validated, confidence-gated, capped LLM technique hits."""
    from shared.itm.index import load_itm_index

    by_id = {tech.id.upper(): tech for tech in load_itm_index().techniques}
    have = {hit.id.upper() for hit in lexical_hits}
    out: list[ItmHit] = []
    for ref in refs:
        tech = by_id.get(str(ref.id).strip().upper())
        if tech is None or tech.id.upper() in have:
            continue
        if float(ref.confidence) < MIN_ITM_REF_CONFIDENCE:
            continue
        have.add(tech.id.upper())
        out.append(
            ItmHit(
                id=tech.id,
                title=tech.title,
                theme=tech.theme,
                article_id=tech.article_id,
                matched_aliases=[],
                source="llm",
            )
        )
        if len(out) >= MAX_LLM_ITM_HITS:
            break
    return out


def merge_llm_hits(entities: ExtractedEntities, llm_hits: list[ItmHit]) -> ExtractedEntities:
    """Fold LLM-adjudicated techniques into entities (id de-dupe, controls re-resolved)."""
    if not llm_hits:
        return entities
    have = {hit.id.upper() for hit in entities.itm_hits}
    fresh = [hit for hit in llm_hits if hit.id.upper() not in have]
    if not fresh:
        return entities
    merged_hits = [*entities.itm_hits, *fresh]
    merged_hits.sort(key=lambda h: (h.theme, h.id))
    detections, preventions = resolve_controls(merged_hits)
    keywords = list(entities.keywords_hit)
    for hit in fresh:
        if hit.id not in keywords:
            keywords.append(hit.id)
    return entities.model_copy(
        update={
            "itm_hits": merged_hits,
            "keywords_hit": keywords,
            "related_detections": detections,
            "related_preventions": preventions,
        }
    )


def _coerce_itm_refs(raw: object) -> list[ItmRef]:
    """Build ItmRef objects from the LLM's raw ``itm_refs`` list, dropping junk."""
    refs: list[ItmRef] = []
    if not isinstance(raw, list):
        return refs
    for item in raw:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            continue
        try:
            conf = float(item.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        evidence = item.get("evidence")
        refs.append(
            ItmRef(
                id=str(item["id"]).strip(),
                confidence=max(0.0, min(1.0, conf)),
                evidence=str(evidence).strip()[:200] if isinstance(evidence, str) else None,
            )
        )
    return refs


def enrich_fields(
    *,
    title: str,
    source: str,
    text: str,
    lexical_hits: list[ItmHit],
    use_cases: list[str],
    settings: Settings,
    budget: SummaryBudget,
) -> tuple[str | None, PerCaseForensics | None, CaseRecord | None, list[ItmHit]]:
    """Run the unified enricher LLM for one article. Never raises.

    Returns (ai_summary, forensics, case_record, llm_itm_hits) — all empty when
    the provider is off, the article doesn't qualify, the budget is exhausted,
    or the call/parse fails. The forensic record is stamped by the caller with
    the article link/title and the final merged ``candidate_technique_ids``.
    """
    empty: tuple[str | None, PerCaseForensics | None, CaseRecord | None, list[ItmHit]] = (
        None,
        None,
        None,
        [],
    )
    provider = get_summarizer_provider(settings)
    if provider is None:
        return empty
    if not qualifies(
        itm_hits=lexical_hits,
        use_cases=use_cases,
        channel=resolve_channel(source),
        text=text,
        filing_min_chars=settings.summarizer_filing_min_text_chars,
    ):
        return empty
    if not budget.take():
        return empty

    # Court filings get the bigger prompt budget — full-document extraction is
    # the point there. The provider's own cap is the max of both settings, so
    # this per-channel truncation is the effective one.
    cap = (
        settings.summarizer_filings_max_input_chars
        if resolve_channel(source) == "filings"
        else settings.summarizer_max_input_chars
    )
    text = (text or "")[:cap]

    candidates = build_itm_candidates(text, lexical_hits)
    try:
        raw = provider.extract_case(
            title=title, source=source, text=text, itm_candidates=candidates
        )
    except Exception as exc:  # noqa: BLE001 — a failed enrichment must not sink the article
        logger.warning("Enricher failed for %r: %s", title[:80], exc)
        return empty
    if not raw:
        logger.warning("Enricher returned nothing for %r", title[:80])
        return empty

    forensics = parse_forensics_json(raw, link="", title=title).model_copy(
        update={
            "extracted_at": datetime.now(UTC),
            "model": getattr(provider, "model_name", None),
        }
    )
    summary = (str(raw.get("ai_summary") or "")).strip() or None
    record = case_record_from_forensics(forensics)
    llm_hits = _validate_itm_refs(_coerce_itm_refs(raw.get("itm_refs")), lexical_hits)
    logger.info(
        "Case enriched for %r (insider=%s, confidence=%.2f, methods=%d, llm_itm=%d)",
        title[:70],
        forensics.is_insider_case,
        forensics.confidence,
        len(forensics.methods),
        len(llm_hits),
    )
    return summary, forensics, record, llm_hits
