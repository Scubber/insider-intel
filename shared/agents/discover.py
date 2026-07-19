"""Novel-technique discovery pass — one LLM call over the forensic record.

Runs after enrichment (``shared/agents/summarize.py``): consumes the extracted
``PerCaseForensics`` (never the raw filing) plus an ITM shortlist, and asks the
model, per method, whether the behavior maps to an existing ITM technique or is
novel. Output is coerced into ``CaseDiscovery`` (never raises) and persisted on
``ProcessedArticle.discovery``. The corpus-level aggregation into candidate
techniques lives in ``apps/aggregator/technique_seeds.py``.
"""

from __future__ import annotations

import json
import logging

from shared.agents.summarize import SummaryBudget, _technique_vectors
from shared.llm import get_discoverer_chain
from shared.schemas.discovery import CaseDiscovery, parse_discovery_json
from shared.schemas.forensics import PerCaseForensics
from shared.settings import Settings
from shared.utils.embeddings import cosine_similarity, get_default_embedder

logger = logging.getLogger(__name__)

SHORTLIST_SIZE = 20
_CANDIDATE_DESC_CHARS = 150


def _method_text(forensics: PerCaseForensics) -> str:
    """A single blob of the record's behavior, for embedding the shortlist."""
    parts: list[str] = []
    for method in forensics.methods:
        parts.append(method.action)
        parts.extend(method.tools)
        if method.target_data:
            parts.append(method.target_data)
    return " ".join(p for p in parts if p)


def build_discovery_shortlist(forensics: PerCaseForensics, *, k: int = SHORTLIST_SIZE) -> str:
    """ITM techniques most relevant to this record — the LLM's map-or-novel set.

    Already-mapped ids (``candidate_technique_ids``) lead, then the nearest
    techniques by hashing-embedding similarity of the record's method text.
    Same line format as ``summarize.build_itm_candidates``.
    """
    from shared.itm.index import load_itm_index

    by_id = {tech.id: tech for tech in load_itm_index().techniques}
    if not by_id:
        return ""

    chosen: list[str] = []
    seen: set[str] = set()
    for tid in forensics.candidate_technique_ids:
        match = next((cid for cid in by_id if cid.upper() == tid.upper()), None)
        if match and match not in seen:
            seen.add(match)
            chosen.append(match)

    vec = get_default_embedder().embed(_method_text(forensics))
    if any(vec):
        scored = [
            (cosine_similarity(vec, tvec), tech_id)
            for tech_id, tvec in _technique_vectors()
            if tech_id not in seen
        ]
        scored.sort(reverse=True)
        for _, tech_id in scored[: max(0, k - len(chosen))]:
            chosen.append(tech_id)

    lines = []
    for tech_id in chosen[:k]:
        tech = by_id[tech_id]
        desc = (tech.description_text or "").strip().replace("\n", " ")
        lines.append(f"{tech.id} — {tech.title} ({tech.theme}): {desc[:_CANDIDATE_DESC_CHARS]}")
    return "\n".join(lines)


def discover_case(
    *, forensics: PerCaseForensics, settings: Settings, budget: SummaryBudget
) -> CaseDiscovery | None:
    """Run the discovery LLM for one enriched case. Never raises.

    Returns ``None`` when the provider is off, the case doesn't qualify (not an
    insider case / no methods), the budget is exhausted, or the call/parse
    fails — the caller then leaves ``ProcessedArticle.discovery`` null.
    """
    if not forensics.is_insider_case or not forensics.methods:
        return None
    chain = get_discoverer_chain(settings)
    if not chain:
        return None
    if not budget.take():
        return None

    forensics_json = json.dumps(
        forensics.model_dump(mode="json", exclude_none=True), ensure_ascii=False
    )
    shortlist = build_discovery_shortlist(forensics)
    # Fallback chain: budget taken once, so retries after a failure are free.
    raw = None
    used_model = None
    for provider in chain:
        try:
            raw = provider.discover_techniques(
                forensics_json=forensics_json, itm_shortlist=shortlist
            )
        except Exception as exc:  # noqa: BLE001 — provider failures never sink an article
            logger.warning(
                "Discovery %s failed for %s: %s",
                getattr(provider, "model_name", "?"),
                forensics.link,
                exc,
            )
            raw = None
        if raw is not None:
            used_model = getattr(provider, "model_name", None)
            break
    if raw is None:
        return None

    discovery = parse_discovery_json(
        raw, forensics=forensics, link=forensics.link, title=forensics.title
    )
    from datetime import UTC, datetime

    discovery = discovery.model_copy(
        update={
            "discovered_at": datetime.now(UTC),
            "model": used_model,
        }
    )
    novel = len(discovery.novel_assessments())
    logger.info(
        "Discovery for %r: %d assessments, %d novel",
        forensics.title,
        len(discovery.assessments),
        novel,
    )
    return discovery
