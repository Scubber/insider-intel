"""Novel-technique discovery models — the second-pass output + corpus view.

The discovery pass runs one LLM call per enriched case (``shared/agents/
discover.py``) over the already-extracted ``PerCaseForensics`` record (never the
raw filing). Per method it decides whether the behavior maps to an existing
Insider Threat Matrix technique or is **novel**, naming the portable behavior.
Its per-case output (``CaseDiscovery``) is persisted on
``ProcessedArticle.discovery``; the refresh job aggregates novel behaviors across
the corpus into ``NovelCandidate`` clusters (``apps/aggregator/technique_seeds``)
with an auto-computed lifecycle (seed → corroborated → eligible). Eligible
candidates are flagged for human review — never auto-minted into a permanent
technique id.

Like ``forensics.py``, ``parse_discovery_json`` is lenient and never raises — a
malformed reply degrades to no assessments rather than sinking an article.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from shared.schemas.forensics import CaseMethod, PerCaseForensics

Disposition = Literal["mapped", "novel"]
EvidenceStrength = Literal["weak", "moderate", "strong"]
CandidateStatus = Literal["seed", "corroborated", "eligible"]

_CLAIM_STRONG = ("admitted", "adjudicated")
_CLAIM_WEAK = ("alleged", "unclear")


class NovelBehavior(BaseModel):
    """A behavior a case shows that no ITM technique covers."""

    label: str = ""
    portable_behavior: str = Field(
        default="",
        description="The reusable behavior, phrased independent of this case's tools/actors",
    )
    case_specific_procedure: str = Field(
        default="",
        description="The concrete way THIS case did it (tool/quantity-specific)",
    )
    distinctness_rationale: str = Field(
        default="",
        description="Why this is not just an instance of an existing ITM technique",
    )


class MethodAssessment(BaseModel):
    """The discovery verdict for one forensic method."""

    method_index: int
    action_summary: str = ""
    disposition: Disposition = "mapped"
    mapped_itm_id: str | None = None
    novel: NovelBehavior | None = None
    # Derived in code from the method's claim_status + observable basis — never
    # trusted from the LLM (it must not self-rate the evidence under its claim).
    evidence_strength: EvidenceStrength = "weak"


class CaseDiscovery(BaseModel):
    """Per-case second-pass output, stored on ``ProcessedArticle.discovery``."""

    link: str
    title: str
    assessments: list[MethodAssessment] = Field(default_factory=list)
    discovered_at: datetime | None = None
    model: str | None = None
    discovery_status: Literal["llm", "skipped"] = "llm"

    def novel_assessments(self) -> list[MethodAssessment]:
        return [a for a in self.assessments if a.disposition == "novel" and a.novel is not None]


class SupportingCase(BaseModel):
    """One case backing a novel candidate (a member of its cluster)."""

    link: str
    title: str = ""
    source_domain: str = ""
    story_key: str = ""
    evidence_strength: EvidenceStrength = "weak"
    claim_status: str = "unclear"


class NovelCandidate(BaseModel):
    """One clustered novel behavior — the persisted + served discovery unit.

    ``id`` is a provisional content hash (``NOVEL-…``), explicitly NOT an ITM
    id: eligible candidates are flagged for review, never auto-promoted.
    """

    id: str
    label: str = ""
    portable_behavior: str = ""
    status: CandidateStatus = "seed"
    flagged_for_review: bool = False
    corroboration_count: int = 0
    distinct_domains: int = 0
    max_itm_similarity: float = 0.0
    nearest_itm_id: str | None = None
    evidence_strength: EvidenceStrength = "weak"
    supporting_cases: list[SupportingCase] = Field(default_factory=list)
    first_seen: datetime | None = None
    last_seen: datetime | None = None


class CandidateCatalogResponse(BaseModel):
    """The corpus-level novel-candidate view, served at /techniques/candidates."""

    generated_at: datetime | None = None
    candidate_count: int = 0
    counts_by_status: dict[str, int] = Field(default_factory=dict)
    candidates: list[NovelCandidate] = Field(default_factory=list)


def derive_evidence_strength(method: CaseMethod) -> EvidenceStrength:
    """How trustworthy this method's evidence is — for the promotion gate.

    strong  = an admitted/adjudicated claim AND at least one mechanically-implied
              observable (a trace the action necessarily leaves).
    weak    = only an alleged/unclear claim AND no observable, or only
              analyst-inferred ones (nothing a defender would actually see).
    moderate= everything in between.
    """
    claim = (getattr(method, "claim_status", "") or "").strip().lower()
    observables = list(getattr(method, "observables", None) or [])
    has_mechanical = any(
        (getattr(o, "basis", "") or "") == "mechanically_implied" for o in observables
    )
    all_inferred = bool(observables) and all(
        (getattr(o, "basis", "") or "") == "analyst_inference" for o in observables
    )
    if claim in _CLAIM_STRONG and has_mechanical:
        return "strong"
    if claim in _CLAIM_WEAK and (not observables or all_inferred):
        return "weak"
    return "moderate"


def _s(value: object, limit: int) -> str:
    return str(value).strip()[:limit] if isinstance(value, str) else ""


def _parse_novel(value: object) -> NovelBehavior | None:
    if not isinstance(value, dict):
        return None
    label = _s(value.get("label"), 160)
    portable = _s(value.get("portable_behavior"), 400)
    if not (label or portable):
        return None
    return NovelBehavior(
        label=label,
        portable_behavior=portable,
        case_specific_procedure=_s(value.get("case_specific_procedure"), 400),
        distinctness_rationale=_s(value.get("distinctness_rationale"), 400),
    )


def parse_discovery_json(
    data: object,
    *,
    forensics: PerCaseForensics,
    link: str,
    title: str,
) -> CaseDiscovery:
    """Lenient coercion of the discovery LLM reply — bad entries drop, never raise.

    ``mapped_itm_id`` is validated against the catalog; an unknown id with a
    novel block present is treated as novel, otherwise the assessment drops.
    ``evidence_strength`` is (re)computed from the matching forensic method — the
    model's own rating, if any, is ignored.
    """
    from shared.itm.index import load_itm_index

    methods = list(getattr(forensics, "methods", None) or [])
    valid_ids = {t.id.upper() for t in load_itm_index().techniques}

    assessments: list[MethodAssessment] = []
    raw_list = data.get("assessments") if isinstance(data, dict) else None
    if not isinstance(raw_list, list):
        raw_list = []
    for raw in raw_list[:40]:
        if not isinstance(raw, dict):
            continue
        try:
            idx = int(raw.get("method_index"))
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(methods):
            continue
        novel = _parse_novel(raw.get("novel"))
        mapped = _s(raw.get("mapped_itm_id"), 20).upper() or None
        disposition = str(raw.get("disposition") or "").strip().lower()
        if disposition == "novel" or (novel is not None and mapped not in valid_ids):
            if novel is None:
                continue
            disposition = "novel"
            mapped = None
        else:
            if mapped not in valid_ids:
                continue
            disposition = "mapped"
            novel = None
        assessments.append(
            MethodAssessment(
                method_index=idx,
                action_summary=_s(raw.get("action_summary"), 300)
                or _s(getattr(methods[idx], "action", ""), 300),
                disposition=disposition,  # type: ignore[arg-type]
                mapped_itm_id=mapped,
                novel=novel,
                evidence_strength=derive_evidence_strength(methods[idx]),
            )
        )
    return CaseDiscovery(link=link, title=title, assessments=assessments)
