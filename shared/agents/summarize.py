"""Ingest enricher: one LLM call → ai_summary + forensic record + ITM adjudication.

Shared by the article-processor graph node and the pipeline backfill sweep.
Every failure path degrades to "no enrichment" — a missing record is never an
error, and the heuristics-only pipeline behaves exactly as before when
SUMMARIZER_LLM_PROVIDER is unset. The legacy ``CaseRecord`` is derived from the
forensic record so the existing analyst-note UI keeps working unchanged.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shared.itm.controls import resolve_controls
from shared.llm import ItmRef, get_summarizer_chain
from shared.schemas.articles import CaseRecord, ExtractedEntities, ItmHit, resolve_channel
from shared.schemas.forensics import (
    PerCaseForensics,
    case_record_from_forensics,
    parse_forensics_json,
    stamp_quote_verbatim,
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
    filing_requires_body: bool = True,
    itm_alignment: str | None = None,
) -> bool:
    """Whether an article clears the insider-signal bar for a downstream action.

    Court filings are pre-filtered insider-relevant by the CourtListener query,
    so a filing with its full body present (``text``/clean_text at or above
    ``filing_min_chars``) qualifies even without a lexical hit.

    ``filing_requires_body`` is the enrichment-vs-acquisition switch:

    * ``True`` (default — the **enrichment spend** gate): a filing must carry the
      real document body. Nearly every filing alias-matches an itm_hit on its
      docket metadata, but a metadata stub has nothing to summarize, so an
      itm_hit alone must NOT unlock a paid LLM call — require the body.
    * ``False`` (the **PACER purchase-eligibility** gate): the whole point there
      is to ACQUIRE the body of an insider-relevant stub, so a lexical/use-case
      hit on the docket metadata is sufficient signal to spend money buying it.
      Requiring a body here would make every purchase candidate ineligible —
      the case can't have a body yet, that's why we're buying it.

    ``itm_alignment`` tightens the non-filings bar when the caller knows it
    (the classify_itm_alignment verdict): enrichment should spend ONLY where a
    forensic extraction is plausible. A classified use case always qualifies
    (a concrete insider scenario), and so does alignment=="insider" (ITM hit +
    framing/motive). A bare weak lexical hit — the "Zimbra CVE roundup matched
    one alias" class — does NOT: those calls come back insider=False,
    methods=0, pure spend. ``None`` (callers without an alignment verdict,
    e.g. the PACER gate) keeps the permissive any-hit behavior.

    A bodied filing additionally needs an insider signal IN THE BODY. The
    per-article itm_hits fire off docket metadata (which embeds the query tag
    that found the case), so they say nothing about the document itself — and
    the corpus audit showed 2/3 of body-length-only enrichments came back
    is_insider_case=False (Valnet v. Google-class dockets that merely matched
    query language). Since 2026-08 the body signal is TWO-part: an ITM alias
    hit AND an insider-framing keyword, both in the body — the 2026-08-04
    audit found a lone alias passes company-v-company IP litigation (58% of
    post-gate enrichments adjudicated non-insider). The lane's own ingest
    match marker ("CourtListener query: …" / "IndiaCourts match: …" lines) is
    stripped before both the length floor and the signal scan, so a marker
    phrased in insider terms can never qualify its own document. A skipped
    filing re-qualifies automatically once a later pass classifies it
    (use_cases / alignment).

    News (``channel=="news"``) must carry a lexical technique hit — the
    use-cases-only path stays open for social/tips confessions, where
    first-person framing is the signal, but vendor commentary that merely
    frames a use case bills as methods=0 non-cases.
    """
    body = strip_match_markers(text or "").strip()
    if channel == "filings" and filing_requires_body:
        if len(body) < max(1, filing_min_chars):
            return False
        return bool(use_cases) or itm_alignment == "insider" or _body_has_insider_signal(body)
    if use_cases and channel != "news":
        return True
    if itm_hits and (itm_alignment is None or itm_alignment == "insider"):
        return True
    if channel == "filings" and len(body) >= max(1, filing_min_chars):
        return bool(use_cases) or itm_alignment == "insider" or _body_has_insider_signal(body)
    return False


# Ingest-time match markers court lanes write into RawArticle.content
# ("scored but hidden"). They embed the very insider terms the gate scans
# for, so they are stripped before any body-signal decision. Two passes,
# because the corpus stores TWO shapes: raw content keeps line structure
# (line pass), but clean_text is whitespace-FLATTENED by to_plain_text, so a
# marker sits mid-string there and only a bounded segment wipe can remove it.
_MATCH_MARKER_PREFIXES: tuple[str, ...] = (
    "courtlistener query:",
    "indiacourts match:",
)
# Longest shipped CourtListener query is ~204 chars; wiping the prefix plus
# this window removes the whole query from flattened text at the cost of at
# most a couple hundred chars of real body — a conservative bias under the
# 1,500-char filings floor. The window is TEMPERED (never consumes a
# following marker's prefix) and the wipe loops to a fixed point, so
# adjacent or straddling markers can't shield each other's residue
# (tests/test_summarize.py pins every shipped query inside the window).
_MARKER_SEGMENT_RE = re.compile(
    r"(?:courtlistener query:|indiacourts match:)"
    r"(?:(?!courtlistener query:|indiacourts match:).){0,240}",
    re.IGNORECASE | re.DOTALL,
)


def strip_match_markers(text: str) -> str:
    """Remove ingest match markers so they can't self-qualify a body.

    Handles both stored shapes: marker LINES in raw content, and marker
    SEGMENTS inside whitespace-flattened clean_text (where splitlines() is a
    no-op — the 2026-08-22 review caught exactly that).
    """
    if not text:
        return text
    lowered = text.lower()
    if not any(prefix in lowered for prefix in _MATCH_MARKER_PREFIXES):
        return text
    kept = "\n".join(
        line
        for line in text.splitlines()
        if not line.strip().lower().startswith(_MATCH_MARKER_PREFIXES)
    )
    for _ in range(8):  # fixed point; bounded against pathological inputs
        wiped = _MARKER_SEGMENT_RE.sub(" ", kept)
        if wiped == kept:
            return wiped
        kept = wiped
    return kept


# A filing must NAME its offense repeatedly to bill on the strong path.
# Prosecutions for insider trading or embezzlement say so throughout the
# document; a statute-title citation, a policy mention, or a precedent quote
# is a singleton. Guards the two bare-phrase DEFAULT_QUERIES lanes, whose
# every row contains its admitting phrase by construction (2026-08-22
# adversarial review).
_STRONG_OFFENSE_MIN_MENTIONS = 3


def strong_offense_hits(body: str) -> tuple[str, ...]:
    """STRONG_INSIDER_OFFENSES phrases the body names as its subject.

    Counts mentions after excising STRONG_OFFENSE_BOILERPLATE (statute
    titles, policy names); an offense qualifies at
    ``_STRONG_OFFENSE_MIN_MENTIONS``. Exposed for the replay script's
    strong-only bucket — keep it in lockstep with _body_has_insider_signal.
    """
    from shared.itm.aliases import STRONG_INSIDER_OFFENSES, STRONG_OFFENSE_BOILERPLATE

    lowered = body.lower()
    if not any(offense in lowered for offense in STRONG_INSIDER_OFFENSES):
        return ()
    for boilerplate in STRONG_OFFENSE_BOILERPLATE:
        lowered = lowered.replace(boilerplate, " ")
    return tuple(
        offense
        for offense in STRONG_INSIDER_OFFENSES
        if lowered.count(offense) >= _STRONG_OFFENSE_MIN_MENTIONS
    )


def _body_has_insider_signal(body: str) -> bool:
    """Does the body carry an insider signal strong enough to bill on?

    Two ways to pass, both pure string scans:

    1. A repeatedly-named STRONG_INSIDER_OFFENSES phrase — offense names that
       ARE ITM infringements ("insider trading" = IF016.004, "embezzlement" =
       IF016), counted by strong_offense_hits. The 2026-08-22 replay showed
       securities prosecutions carry no employment-framing vocabulary at all,
       so the two-part rule below was blocking the most canonical insider
       cases in the corpus.
    2. An ITM alias hit AND an insider-framing keyword. A lone ordinary alias
       proved too weak a bar (2026-08-04 audit: 58% of post-gate filings
       enrichments adjudicated non-insider — company-v-company IP litigation
       clears one alias trivially); framing anchors it to an insider scenario.
    """
    from shared.utils.entities import find_framing_keywords, match_itm_techniques

    if strong_offense_hits(body):
        return True
    return bool(match_itm_techniques(body)) and bool(find_framing_keywords(body))


def article_qualifies(
    article,
    *,
    filing_min_chars: int = 1_500,
    filing_requires_body: bool = True,
    use_itm_alignment: bool = False,
) -> bool:
    """`qualifies` for a ProcessedArticle-shaped object (backfill / PACER paths).

    ``filing_requires_body`` mirrors :func:`qualifies` — the enrichment backfill
    leaves it ``True`` (body required); the PACER purchaser passes ``False`` so
    it can target the bodyless stubs it exists to acquire.

    ``use_itm_alignment=True`` (the enrichment backfill) reads the row's stored
    ``itm_alignment`` so weak-hit news never spends; the PACER path leaves it
    ``False`` — CourtListener already pre-filtered those dockets, and a
    watchlist catch-all match carries no framing language yet is exactly what
    the operator asked to buy.
    """
    entities = getattr(article, "entities", None)
    hits = list(getattr(entities, "itm_hits", None) or [])
    alignment = getattr(article, "itm_alignment", None) if use_itm_alignment else None
    return qualifies(
        itm_hits=hits,
        use_cases=list(getattr(article, "use_cases", None) or []),
        channel=resolve_channel(getattr(article, "source_id", "") or ""),
        text=getattr(article, "clean_text", "") or "",
        filing_min_chars=filing_min_chars,
        filing_requires_body=filing_requires_body,
        itm_alignment=alignment,
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
    itm_alignment: str | None = None,
) -> tuple[str | None, PerCaseForensics | None, CaseRecord | None, list[ItmHit]]:
    """Run the unified enricher LLM for one article. Never raises.

    Returns (ai_summary, forensics, case_record, llm_itm_hits) — all empty when
    the provider is off, the article doesn't qualify, the budget is exhausted,
    or the call/parse fails. The forensic record is stamped by the caller with
    the article link/title and the final merged ``candidate_technique_ids``.
    ``itm_alignment`` (the classify verdict) tightens the non-filings gate:
    weak-hit articles yield no extractable forensics, so they must not spend.
    """
    empty: tuple[str | None, PerCaseForensics | None, CaseRecord | None, list[ItmHit]] = (
        None,
        None,
        None,
        [],
    )
    chain = get_summarizer_chain(settings)
    if not chain:
        return empty
    if not qualifies(
        itm_hits=lexical_hits,
        use_cases=use_cases,
        channel=resolve_channel(source),
        text=text,
        filing_min_chars=settings.summarizer_filing_min_text_chars,
        itm_alignment=itm_alignment,
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
    # Fallback chain: try each provider until one returns a usable reply. Budget
    # is taken once above, so fallbacks after a failure never cost extra spend.
    raw = None
    used_model = None
    for provider in chain:
        try:
            raw = provider.extract_case(
                title=title, source=source, text=text, itm_candidates=candidates
            )
        except Exception as exc:  # noqa: BLE001 — a failed provider must not sink the article
            logger.warning(
                "Enricher %s failed for %r: %s",
                getattr(provider, "model_name", "?"),
                title[:80],
                exc,
            )
            raw = None
        if raw:
            used_model = getattr(provider, "model_name", None)
            break
    if not raw:
        logger.warning("All enrichment providers failed/empty for %r", title[:80])
        return empty

    forensics = parse_forensics_json(raw, link="", title=title).model_copy(
        update={
            "extracted_at": datetime.now(UTC),
            "model": used_model,
        }
    )
    # Grounding stamp against the text the model actually saw (post-truncation):
    # quotes it could not have copied are paraphrases by definition.
    stamp_quote_verbatim(forensics, text)
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
