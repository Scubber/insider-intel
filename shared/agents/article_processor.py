"""LangGraph agent: raw article → processed article.

MVP graph: normalize → extract_entities → score → classify → summarize →
embed → assemble. Heuristics + local hashing embeddings (no external LLM/API
required); optional LLM stages refine classification (CLASSIFIER_LLM_PROVIDER)
and write ai_summary / case_record / LLM-adjudicated ITM hits
(SUMMARIZER_LLM_PROVIDER).
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from shared.agents.summarize import SummaryBudget, enrich_fields, merge_llm_hits
from shared.llm import get_classifier_provider
from shared.schemas import ProcessedArticle, RawArticle
from shared.schemas.articles import ExtractedEntities, ItmHit, resolve_channel
from shared.schemas.forensics import (
    EnrichmentRecord,
    PerCaseForensics,
    append_enrichment,
    case_record_from_forensics,
    project_additive_fields,
    select_best_enrichment,
)
from shared.settings import get_settings
from shared.utils.classify import classify_insider_type, classify_use_cases
from shared.utils.embeddings import get_default_embedder
from shared.utils.entities import classify_itm_alignment, extract_entities, score_relevance
from shared.utils.story_key import (
    compute_story_key,
    filing_story_key,
    parse_filing_reference,
)
from shared.utils.text import to_plain_text

logger = logging.getLogger(__name__)


class ArticleProcessState(TypedDict, total=False):
    """Explicit state machine for one article."""

    raw: dict[str, Any]
    clean_text: str
    entities: dict[str, Any]
    relevance_score: float
    use_cases: list[str]
    insider_type: str | None
    classification_source: str | None
    classification_confidence: float | None
    ai_summary: str | None
    case_record: dict[str, Any] | None
    forensics: dict[str, Any] | None
    enrichment_history: list[dict[str, Any]]
    discovery: dict[str, Any] | None
    # Carry-forward inputs so reprocessing never re-bills the LLM
    prior_ai_summary: str | None
    prior_case_record: dict[str, Any] | None
    prior_forensics: dict[str, Any] | None
    prior_enrichment_history: list[dict[str, Any]]
    prior_discovery: dict[str, Any] | None
    prior_llm_itm_hits: list[dict[str, Any]]
    budget: Any
    discover_budget: Any
    embedding: list[float]
    processed: dict[str, Any] | None
    error: str | None


def _node_normalize(state: ArticleProcessState) -> ArticleProcessState:
    raw = RawArticle.model_validate(state["raw"])
    parts = [raw.title, raw.summary or "", raw.content or ""]
    clean = to_plain_text("\n".join(parts))
    return {"clean_text": clean, "error": None}


def _node_extract_entities(state: ArticleProcessState) -> ArticleProcessState:
    text = state.get("clean_text") or ""
    entities = extract_entities(text)
    return {"entities": entities.model_dump()}


def _node_score(state: ArticleProcessState) -> ArticleProcessState:
    entities = ExtractedEntities.model_validate(state.get("entities") or {})
    text = state.get("clean_text") or ""
    score = score_relevance(entities, text_length=len(text))
    return {"relevance_score": score}


def _node_classify(state: ArticleProcessState) -> ArticleProcessState:
    raw = RawArticle.model_validate(state["raw"])
    entities = ExtractedEntities.model_validate(state.get("entities") or {})
    text = state.get("clean_text") or ""

    # The heuristics corroborate insider_type/use_cases off ITM Motive hits,
    # but at this node the entity view is lexical-only — LLM-adjudicated hits
    # carried from a prior enrichment are merged later, in summarize. On a
    # re-tag reprocess (--force sweep) that ordering hid already-paid-for
    # Motive adjudications from the classifier, so an enriched insider case
    # whose text carries no cue phrases stayed insider_type=None forever.
    # Merge the carried hits for classification only; the stored entity view
    # is still produced by the summarize node's own merge.
    prior_llm_hits = [ItmHit.model_validate(h) for h in state.get("prior_llm_itm_hits") or []]
    if prior_llm_hits:
        entities = merge_llm_hits(entities, prior_llm_hits)

    use_cases = classify_use_cases(text, entities)
    insider_type = classify_insider_type(text, entities)
    source: str | None = "heuristic" if (use_cases or insider_type) else None
    confidence: float | None = None

    settings = get_settings()
    provider = get_classifier_provider(settings)
    if provider is not None and _llm_gate_passes(settings, raw, use_cases, insider_type):
        try:
            result = provider.classify(title=raw.title, text=text)
        except Exception:
            logger.warning("LLM classification failed for %s; keeping heuristic", raw.link)
        else:
            if result is not None and result.confidence >= 0.6:
                use_cases = result.use_cases
                insider_type = result.insider_type
                source = "llm"
                confidence = result.confidence

    return {
        "use_cases": use_cases,
        "insider_type": insider_type,
        "classification_source": source,
        "classification_confidence": confidence,
    }


def _llm_gate_passes(
    settings,
    raw: RawArticle,
    use_cases: list[str],
    insider_type: str | None,
) -> bool:
    """Only spend LLM calls where heuristics are thin, on configured channels."""
    channels = settings.classify_llm_channel_list()
    channel = resolve_channel(raw.source_id, raw.channel)
    if "all" not in channels and channel not in channels:
        return False
    return not use_cases or insider_type is None


def _prior_enrichment_history(state: ArticleProcessState) -> list[EnrichmentRecord]:
    """Reconstruct the carried-forward enrichment history (with legacy seed).

    Rows written before enrichment versioning have no ``enrichment_history`` but
    do carry a single ``forensics`` record; seed history from it so the first
    reprocess preserves that generation instead of starting empty. Malformed
    entries are skipped, never raised — history must not sink an article.
    """
    history: list[EnrichmentRecord] = []
    for item in state.get("prior_enrichment_history") or []:
        try:
            history.append(EnrichmentRecord.model_validate(item))
        except Exception:  # noqa: BLE001 — a bad archived generation must not raise
            logger.warning("Skipping unparseable prior enrichment generation")
    if not history:
        pf = state.get("prior_forensics")
        if pf:
            try:
                history.append(
                    EnrichmentRecord(
                        ai_summary=state.get("prior_ai_summary"),
                        forensics=PerCaseForensics.model_validate(pf),
                    )
                )
            except Exception:  # noqa: BLE001 — legacy seed is best-effort
                logger.warning("Could not seed enrichment history from legacy forensics")
    return history


def _emit_selected(
    history: list[EnrichmentRecord], merged: ExtractedEntities
) -> ArticleProcessState:
    """Project the append-only history to the selected-best view the row stores.

    ``forensics`` / ``ai_summary`` / ``case_record`` are the richest generation
    (:func:`select_best_enrichment`); the full history rides alongside so no
    generation is ever lost. case_record is re-derived from the selected
    forensics exactly as the enricher derived it — keeping one source of truth.
    """
    best = select_best_enrichment(history)
    # Additive-field overlay (ADDITIVE_FIELDS): fill a None additive value from
    # the newest same-tier generation that has one — never a selection input.
    forensics = project_additive_fields(history, best)
    record = case_record_from_forensics(forensics) if forensics is not None else None
    return {
        "ai_summary": best.ai_summary if best is not None else None,
        "case_record": record.model_dump(mode="json") if record is not None else None,
        "forensics": forensics.model_dump(mode="json") if forensics is not None else None,
        "enrichment_history": [rec.model_dump(mode="json") for rec in history],
        "entities": merged.model_dump(),
    }


def _node_summarize(state: ArticleProcessState) -> ArticleProcessState:
    """One LLM call → append a generation to history, then emit the best.

    Enrichment is append-only: a new generation is added to the carried-forward
    history and the richest is selected, so a thin re-run never overwrites a
    richer prior record. Never re-bills a reused generation; never raises.
    """
    entities = ExtractedEntities.model_validate(state.get("entities") or {})
    prior_summary = state.get("prior_ai_summary")
    prior_record = state.get("prior_case_record")
    prior_forensics = state.get("prior_forensics")
    prior_llm_hits = [ItmHit.model_validate(h) for h in state.get("prior_llm_itm_hits") or []]
    # Reuse (no LLM) only when a previously *selected* generation is carried.
    # History presence alone must NOT suppress re-enrichment — a cleared filing
    # (drain / full-text backfill) carries its history for protection but still
    # needs the fresh call. So the cache-hit test excludes prior history.
    if (
        prior_summary is not None
        or prior_record is not None
        or prior_forensics is not None
        or prior_llm_hits
    ):
        merged = merge_llm_hits(entities, prior_llm_hits)
        history = _prior_enrichment_history(state)
        if history:
            return _emit_selected(history, merged)
        # Legacy passthrough: a pre-forensics row carries a case_record/ai_summary
        # but nothing to version (no forensics to seed history from). Preserve it
        # exactly — never let the select-best projection wipe it to None.
        return {
            "ai_summary": prior_summary,
            "case_record": prior_record,
            "forensics": prior_forensics,
            "enrichment_history": [],
            "entities": merged.model_dump(),
        }

    raw = RawArticle.model_validate(state["raw"])
    settings = get_settings()
    budget = state.get("budget") or SummaryBudget(settings.summarizer_max_articles_per_run)
    summary, forensics, _record, llm_hits = enrich_fields(
        title=raw.title,
        source=raw.source_id,
        text=state.get("clean_text") or "",
        lexical_hits=entities.itm_hits,
        use_cases=list(state.get("use_cases") or []),
        settings=settings,
        budget=budget,
        # Spend only where extraction is plausible: weak-alignment news (one
        # stray alias in a CVE roundup) returns methods=0 — skip it.
        itm_alignment=classify_itm_alignment(entities),
    )
    merged = merge_llm_hits(entities, llm_hits)
    history = _prior_enrichment_history(state)
    if forensics is not None:
        # Stamp the article link/title and the final catalog-validated technique
        # ids (lexical ∪ LLM-adjudicated) so the report assembles from one source.
        stamped = forensics.model_copy(
            update={
                "link": raw.link,
                "title": raw.title,
                "candidate_technique_ids": [h.id.upper() for h in merged.itm_hits],
            }
        )
        history = append_enrichment(
            history, EnrichmentRecord(ai_summary=summary, forensics=stamped)
        )
    return _emit_selected(history, merged)


def _node_discover(state: ArticleProcessState) -> ArticleProcessState:
    """Second LLM pass: map the forensic record's methods vs the ITM or flag
    novel behavior. Reads state["forensics"] (never the raw filing); never raises.
    """
    prior_discovery = state.get("prior_discovery")
    if prior_discovery is not None:
        # Cache hit: discovery already paid for on a previous run — reuse it.
        return {"discovery": prior_discovery}

    forensics_payload = state.get("forensics")
    if not forensics_payload:
        return {"discovery": None}

    from shared.agents.discover import discover_case
    from shared.schemas.forensics import PerCaseForensics

    settings = get_settings()
    budget = state.get("discover_budget") or SummaryBudget(settings.discoverer_max_articles_per_run)
    try:
        forensics = PerCaseForensics.model_validate(forensics_payload)
        discovery = discover_case(forensics=forensics, settings=settings, budget=budget)
    except Exception as exc:  # noqa: BLE001 — discovery must never sink an article
        logger.warning("Discovery node failed: %s", exc)
        return {"discovery": None}
    return {"discovery": discovery.model_dump(mode="json") if discovery is not None else None}


def _node_embed(state: ArticleProcessState) -> ArticleProcessState:
    text = state.get("clean_text") or ""
    embedding = get_default_embedder().embed(text)
    return {"embedding": embedding}


def _node_assemble(state: ArticleProcessState) -> ArticleProcessState:
    raw = RawArticle.model_validate(state["raw"])
    entities = ExtractedEntities.model_validate(state.get("entities") or {})
    channel = resolve_channel(raw.source_id, raw.channel)
    story_key = compute_story_key(
        raw.title,
        raw.published,
        fallback=raw.ingested_at,
    )
    if channel == "filings":
        # Cluster court documents by case (court + docket number) so the
        # docket and its opinions group together across days.
        filing_ref = parse_filing_reference(raw.summary)
        if filing_ref:
            story_key = filing_story_key(*filing_ref)
    use_cases = list(state.get("use_cases") or [])
    insider_type = state.get("insider_type")
    itm_alignment = classify_itm_alignment(entities)
    # First-person confessions (social posts) rarely use ITM framing language,
    # but a classified use case + insider disposition IS an insider scenario.
    if itm_alignment == "weak" and use_cases and insider_type:
        itm_alignment = "insider"
    processed = ProcessedArticle(
        title=raw.title,
        link=raw.link,
        published=raw.published,
        source_id=raw.source_id,
        source_name=raw.source_name,
        channel=channel,
        summary=to_plain_text(raw.summary) or None,
        legal_metadata=raw.legal_metadata,
        clean_text=state.get("clean_text") or "",
        entities=entities,
        relevance_score=float(state.get("relevance_score") or 0.0),
        itm_alignment=itm_alignment,
        story_key=story_key,
        use_cases=use_cases,
        insider_type=insider_type,  # type: ignore[arg-type]
        classification_source=state.get("classification_source"),  # type: ignore[arg-type]
        classification_confidence=state.get("classification_confidence"),
        ai_summary=state.get("ai_summary"),
        case_record=state.get("case_record"),
        forensics=state.get("forensics"),
        enrichment_history=state.get("enrichment_history") or [],
        discovery=state.get("discovery"),
        embedding=state.get("embedding"),
    )
    return {"processed": processed.model_dump(mode="json")}


def build_article_processor():
    """Compile the article processing StateGraph."""
    graph = StateGraph(ArticleProcessState)
    graph.add_node("normalize", _node_normalize)
    graph.add_node("extract_entities", _node_extract_entities)
    graph.add_node("score", _node_score)
    graph.add_node("classify", _node_classify)
    graph.add_node("summarize", _node_summarize)
    graph.add_node("discover", _node_discover)
    graph.add_node("embed", _node_embed)
    graph.add_node("assemble", _node_assemble)

    graph.add_edge(START, "normalize")
    graph.add_edge("normalize", "extract_entities")
    graph.add_edge("extract_entities", "score")
    graph.add_edge("score", "classify")
    graph.add_edge("classify", "summarize")
    graph.add_edge("summarize", "discover")
    graph.add_edge("discover", "embed")
    graph.add_edge("embed", "assemble")
    graph.add_edge("assemble", END)

    return graph.compile()


_PROCESSOR = None


def get_article_processor():
    global _PROCESSOR
    if _PROCESSOR is None:
        _PROCESSOR = build_article_processor()
    return _PROCESSOR


def process_article(
    raw: RawArticle,
    *,
    prior: ProcessedArticle | None = None,
    budget: SummaryBudget | None = None,
    discover_budget: SummaryBudget | None = None,
) -> ProcessedArticle:
    """Run the LangGraph processor on a single raw article.

    ``prior`` carries already-paid-for LLM fields (ai_summary / case_record /
    forensics / LLM ITM hits) through a re-process so the enricher is never
    re-billed. ``budget`` shares one per-run LLM allowance across many calls.

    A legacy row (case_record from the old summarizer, no forensics) normally
    carries forward untouched; when ``SUMMARIZER_UPGRADE_LEGACY`` is set it is
    left un-carried so the enricher re-bills it once to add the forensic record.
    """
    initial: dict[str, Any] = {"raw": raw.model_dump(mode="json")}
    if budget is not None:
        initial["budget"] = budget
    if discover_budget is not None:
        initial["discover_budget"] = discover_budget
    prior_forensics = getattr(prior, "forensics", None) if prior is not None else None
    prior_history = getattr(prior, "enrichment_history", None) if prior is not None else None
    upgrade_legacy = get_settings().summarizer_upgrade_legacy and prior_forensics is None
    if prior is not None and prior_history:
        # The enrichment history is the durable archive: carry it forward on
        # EVERY reprocess — even a legacy-upgrade or a cleared-then-re-enrich
        # run — so select-best can restore a richer prior generation and a thin
        # re-run can never gut the card. This is independent of the re-bill
        # (cache-hit) decision below.
        initial["prior_enrichment_history"] = [r.model_dump(mode="json") for r in prior_history]
    if prior is not None and not upgrade_legacy:
        if prior.ai_summary is not None:
            initial["prior_ai_summary"] = prior.ai_summary
        if prior.case_record is not None:
            initial["prior_case_record"] = prior.case_record.model_dump(mode="json")
        if prior_forensics is not None:
            initial["prior_forensics"] = prior_forensics.model_dump(mode="json")
        # Discovery carries independently: a row can have forensics but no
        # discovery yet (added after forensics) — leave prior_discovery unset so
        # the node runs discovery fresh on the carried-forward forensics.
        prior_discovery = getattr(prior, "discovery", None)
        if prior_discovery is not None:
            initial["prior_discovery"] = prior_discovery.model_dump(mode="json")
        prior_llm = [
            hit.model_dump(mode="json")
            for hit in prior.entities.itm_hits
            if getattr(hit, "source", "lexical") == "llm"
        ]
        if prior_llm:
            initial["prior_llm_itm_hits"] = prior_llm

    processor = get_article_processor()
    try:
        result = processor.invoke(initial)
    except Exception as exc:
        logger.exception("Article processor failed for %s", raw.link)
        raise RuntimeError(f"processing failed for {raw.link}: {exc}") from exc

    if result.get("error"):
        raise RuntimeError(result["error"])

    processed_payload = result.get("processed")
    if not processed_payload:
        raise RuntimeError(f"processor returned no processed article for {raw.link}")

    return ProcessedArticle.model_validate(processed_payload)
