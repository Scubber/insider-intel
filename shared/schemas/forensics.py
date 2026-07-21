"""Provider-agnostic forensic-record models for insider cases.

``PerCaseForensics`` is the report-time reconstruction of one case: what the
insider did, the artifacts that behavior leaves in a defender's environment,
and the searchable leads it produces. It is produced by the unified ingest
enricher (``shared/agents/summarize.py``) and persisted on
``ProcessedArticle.forensics``; the board report assembles stored records in
code. The lenient ``parse_forensics_json`` never raises — bad fields drop — so
a malformed LLM reply degrades gracefully rather than sinking an article.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from shared.schemas.articles import CaseRecord

OBSERVABLE_CHANNELS = (
    "email",
    "chat",
    "network",
    "endpoint",
    "cloud",
    "identity",
    "physical",
    "human",
)

ObservableChannel = Literal[
    "email", "chat", "network", "endpoint", "cloud", "identity", "physical", "human"
]

# Whether an observable is a mechanical consequence of the stated action or a
# defender's inference. Unlabeled observables default to the weaker claim.
ObservableBasis = Literal["mechanically_implied", "analyst_inference"]
OBSERVABLE_BASES = ("mechanically_implied", "analyst_inference")

# How strongly the SOURCE frames a method — an allegation must never read as a
# finding. Unlabeled methods default to "unclear".
CLAIM_STATUSES = ("alleged", "admitted", "adjudicated", "reported", "unclear")

# Document provenance / legal stage, validated against these sets; anything
# else falls back to "unknown".
SOURCE_TYPES = ("court_filing", "news", "blog", "social", "press_release", "unknown")
LEGAL_POSTURES = (
    "indictment",
    "complaint",
    "plea",
    "conviction",
    "sentencing",
    "civil_suit",
    "settlement",
    "none",
    "unknown",
)


class CaseObservable(BaseModel):
    """One concrete trace a behavior leaves in a defender's environment."""

    description: str
    artifact: str = Field(
        default="",
        description="Log source / record it appears in, e.g. 'email gateway logs'",
    )
    channel: ObservableChannel = "network"
    basis: ObservableBasis = Field(
        default="analyst_inference",
        description="mechanically_implied (guaranteed by the action) vs analyst_inference",
    )


class CaseMethod(BaseModel):
    """One action the insider took, grounded in the case text."""

    action: str
    tools: list[str] = Field(default_factory=list)
    target_data: str | None = None
    quantity: str | None = None
    claim_status: Literal["alleged", "admitted", "adjudicated", "reported", "unclear"] = Field(
        default="unclear",
        description="How the source frames the action — allegation vs proven finding",
    )
    evidence_quote: str = Field(
        default="",
        description="Short verbatim excerpt from the source supporting this action",
    )
    observables: list[CaseObservable] = Field(default_factory=list)


class HuntQuerySeed(BaseModel):
    """A case-grounded hunt query precomputed at ingest (article-scoped)."""

    stack: str = "SIEM"
    logic: str
    rationale: str = ""


class PerCaseForensics(BaseModel):
    """Forensic reconstruction of one insider case.

    The first block is the reconstruction proper; the second block carries the
    case facts an analyst note is built from (the legacy ``CaseRecord`` is
    derived from these via ``case_record_from_forensics``). All the case-fact
    fields are optional so a report-time floor record (from
    ``forensics_from_floor``) validates with them empty.
    """

    link: str
    title: str
    source_type: str = Field(
        default="unknown", description="Document provenance: court_filing|news|blog|social|…"
    )
    legal_posture: str = Field(
        default="unknown", description="Legal stage: indictment|complaint|plea|conviction|…"
    )
    actor_profile: str = ""
    timeline: list[str] = Field(default_factory=list)
    methods: list[CaseMethod] = Field(default_factory=list)
    detection: str | None = None
    outcome: str | None = None
    candidate_technique_ids: list[str] = Field(default_factory=list)
    hunt_terms: list[str] = Field(default_factory=list)
    hunt_queries: list[HuntQuerySeed] = Field(default_factory=list)
    extraction_status: Literal["llm", "floor"] = "llm"
    # Case facts (feed the analyst note / legacy CaseRecord) — all default-safe.
    is_insider_case: bool = False
    actor_role: str | None = None
    access_vector: str | None = None
    motive_signals: list[str] = Field(default_factory=list)
    exfil_channels: list[str] = Field(default_factory=list)
    timeframe: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    extracted_at: datetime | None = None
    model: str | None = None


def case_record_from_forensics(f: PerCaseForensics) -> CaseRecord:
    """Derive the legacy CaseRecord from a forensic record (UI back-compat).

    ``sanitized()`` clamps lengths and strips control chars, so the derived
    record is safe to render exactly as the ingest summarizer's record was.
    """
    from shared.schemas.articles import CaseRecord

    actor_role = f.actor_role
    if not actor_role and f.actor_profile:
        actor_role = f.actor_profile.split("—")[0].strip() or None
    return CaseRecord(
        is_insider_case=f.is_insider_case,
        actor_role=actor_role,
        access_vector=f.access_vector,
        motive_signals=f.motive_signals,
        methods=[m.action for m in f.methods],
        exfil_channels=f.exfil_channels,
        timeframe=f.timeframe,
        detection_trigger=f.detection,
        outcome=f.outcome,
        confidence=f.confidence,
        extracted_at=f.extracted_at,
        model=f.model,
    ).sanitized()


def _s(value: object, limit: int) -> str:
    return str(value).strip()[:limit] if isinstance(value, str) else ""


def _slist(value: object, limit: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for v in value[:limit]:
        if v is None:
            continue
        cleaned = str(v).strip()[:item_limit]
        if cleaned:
            out.append(cleaned)
    return out


def parse_observables(value: object, *, limit: int = 6) -> list[CaseObservable]:
    """Coerce a raw LLM observables list; bad entries drop, never raise."""
    observables: list[CaseObservable] = []
    if not isinstance(value, list):
        return observables
    for obs in value[:limit]:
        if not isinstance(obs, dict):
            continue
        desc = _s(obs.get("description"), 300)
        if not desc:
            continue
        channel = str(obs.get("channel") or "").strip().lower()
        basis = str(obs.get("basis") or "").strip().lower()
        observables.append(
            CaseObservable(
                description=desc,
                artifact=_s(obs.get("artifact"), 120),
                channel=channel if channel in OBSERVABLE_CHANNELS else "network",
                basis=basis if basis in OBSERVABLE_BASES else "analyst_inference",
            )
        )
    return observables


def parse_hunt_queries(value: object, *, limit: int = 3) -> list[HuntQuerySeed]:
    """Coerce a raw LLM hunt-queries list; bad entries drop, never raise."""
    queries: list[HuntQuerySeed] = []
    if not isinstance(value, list):
        return queries
    for raw in value[:limit]:
        if not isinstance(raw, dict):
            continue
        logic = _s(raw.get("logic"), 600)
        if not logic:
            continue
        queries.append(
            HuntQuerySeed(
                stack=_s(raw.get("stack"), 60) or "SIEM",
                logic=logic,
                rationale=_s(raw.get("rationale"), 300),
            )
        )
    return queries


def _coerce_methods(value: object) -> list[CaseMethod]:
    methods: list[CaseMethod] = []
    if not isinstance(value, list):
        return methods
    for raw in value[:12]:
        if not isinstance(raw, dict):
            continue
        action = _s(raw.get("action"), 400)
        if not action:
            continue
        claim = str(raw.get("claim_status") or "").strip().lower()
        methods.append(
            CaseMethod(
                action=action,
                tools=_slist(raw.get("tools"), 6, 80),
                target_data=_s(raw.get("target_data"), 200) or None,
                quantity=_s(raw.get("quantity"), 100) or None,
                claim_status=claim if claim in CLAIM_STATUSES else "unclear",
                evidence_quote=_s(raw.get("evidence_quote"), 400),
                observables=parse_observables(raw.get("observables")),
            )
        )
    return methods


def parse_forensics_json(data: dict, *, link: str, title: str) -> PerCaseForensics:
    """Lenient coercion of unified-enricher JSON — bad fields drop, never raise.

    Handles both the full enrichment reply (case facts + reconstruction +
    hunt_queries) and the older reconstruction-only shape (extra keys simply
    default). ``candidate_technique_ids`` is stamped by the caller from the
    merged ITM hits, so it is not read here.
    """
    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    source_type = str(data.get("source_type") or "").strip().lower()
    legal_posture = str(data.get("legal_posture") or "").strip().lower()
    return PerCaseForensics(
        link=link,
        title=title,
        source_type=source_type if source_type in SOURCE_TYPES else "unknown",
        legal_posture=legal_posture if legal_posture in LEGAL_POSTURES else "unknown",
        actor_profile=_s(data.get("actor_profile"), 300),
        timeline=_slist(data.get("timeline"), 10, 300),
        methods=_coerce_methods(data.get("methods")),
        # Full-sentence narrative fields — keep generous so the UI's DETECTED
        # VIA / OUTCOME don't get clipped mid-sentence (matches the CaseRecord
        # narrative clamp, _CASE_TEXT_MAX_CHARS).
        detection=_s(data.get("detection"), 800) or None,
        outcome=_s(data.get("outcome"), 800) or None,
        hunt_terms=_slist(data.get("hunt_terms"), 12, 120),
        hunt_queries=parse_hunt_queries(data.get("hunt_queries")),
        is_insider_case=bool(data.get("is_insider_case")),
        actor_role=_s(data.get("actor_role"), 200) or None,
        access_vector=_s(data.get("access_vector"), 200) or None,
        motive_signals=_slist(data.get("motive_signals"), 8, 120),
        exfil_channels=_slist(data.get("exfil_channels"), 8, 120),
        timeframe=_s(data.get("timeframe"), 200) or None,
        confidence=max(0.0, min(1.0, confidence)),
        extraction_status="llm",
    )
