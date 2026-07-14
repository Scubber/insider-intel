"""LangGraph agent: raw article → processed article.

MVP graph: normalize → extract_entities → score → classify → embed → assemble.
Heuristics + local hashing embeddings (no external LLM/API required);
an optional LLM refiner sharpens use-case / insider-type labels.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from shared.llm import get_classifier_provider
from shared.schemas import ProcessedArticle, RawArticle
from shared.schemas.articles import ExtractedEntities, resolve_channel
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
        clean_text=state.get("clean_text") or "",
        entities=entities,
        relevance_score=float(state.get("relevance_score") or 0.0),
        itm_alignment=itm_alignment,
        story_key=story_key,
        use_cases=use_cases,
        insider_type=insider_type,  # type: ignore[arg-type]
        classification_source=state.get("classification_source"),  # type: ignore[arg-type]
        classification_confidence=state.get("classification_confidence"),
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
    graph.add_node("embed", _node_embed)
    graph.add_node("assemble", _node_assemble)

    graph.add_edge(START, "normalize")
    graph.add_edge("normalize", "extract_entities")
    graph.add_edge("extract_entities", "score")
    graph.add_edge("score", "classify")
    graph.add_edge("classify", "embed")
    graph.add_edge("embed", "assemble")
    graph.add_edge("assemble", END)

    return graph.compile()


_PROCESSOR = None


def get_article_processor():
    global _PROCESSOR
    if _PROCESSOR is None:
        _PROCESSOR = build_article_processor()
    return _PROCESSOR


def process_article(raw: RawArticle) -> ProcessedArticle:
    """Run the LangGraph processor on a single raw article."""
    processor = get_article_processor()
    try:
        result = processor.invoke({"raw": raw.model_dump(mode="json")})
    except Exception as exc:
        logger.exception("Article processor failed for %s", raw.link)
        raise RuntimeError(f"processing failed for {raw.link}: {exc}") from exc

    if result.get("error"):
        raise RuntimeError(result["error"])

    processed_payload = result.get("processed")
    if not processed_payload:
        raise RuntimeError(f"processor returned no processed article for {raw.link}")

    return ProcessedArticle.model_validate(processed_payload)
