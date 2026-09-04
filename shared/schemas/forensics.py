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
from typing import TYPE_CHECKING, Literal, NamedTuple

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

# Bumped whenever the stored-field clamps widen (or the record shape changes) so
# a re-enrich sweep can re-select rows enriched under a narrower schema. v1 =
# the original tight clamps (detection/outcome 300, method action 400); v2 =
# storage safety bounds only (detection/outcome 2000, method action 600), full
# narrative persisted. v3 = the 2026-08-21 freeze (docs/schema-freeze-v3.md):
# actor_citizenship + industry + tool_mentions in, hunt_queries out of the
# write path, prompt contract v3. Rows with schema_version < this are
# re-enrich candidates (the #14 sweep arms via SUMMARIZER_REENRICH_* env —
# defaults keep the bump inert until then).
ENRICH_SCHEMA_VERSION = 3

# Document provenance / legal stage, validated against these sets; anything
# else falls back to "unknown".
SOURCE_TYPES = ("court_filing", "news", "blog", "social", "press_release", "unknown")

# Victim organization's sector (v3). Financial services first per operator
# priority; "unknown" when the source is silent.
INDUSTRIES = (
    "financial-services",
    "healthcare",
    "technology",
    "defense",
    "manufacturing",
    "energy",
    "retail",
    "public-sector",
    "professional-services",
    "other",
    "unknown",
)

# How a named product figured in the case (v3) — fills the TOOLING table's
# end-state columns (operator-approved v8 spec): caught = it detected or
# stopped the conduct; bypassed = present but evaded; misused = the insider's
# instrument; traced = used after the fact to reconstruct events.
TOOL_MENTION_ROLES = ("caught", "bypassed", "misused", "traced")
CONTEXT_KINDS = ("detection", "prevention", "tradecraft", "policy", "news")

LEGAL_POSTURES = (
    "indictment",
    "complaint",
    "plea",
    "conviction",
    "sentencing",
    "civil_suit",
    "settlement",
    # Indian procedural stages (2026-08, the IndiaCourts lane). Every value
    # here must ALSO appear in shared/utils/evidence.py::POSTURE_WEIGHT and in
    # the prompt enum in shared/llm/base.py — an unmapped posture silently
    # UNCAPS claim_status in case_strength (tests/test_legal_postures.py is
    # the drift tripwire).
    "fir_allegation",
    "charge_sheet",
    "disciplinary_proceeding",
    "interim_injunction",
    "bail",
    "quashing",
    "writ_review",
    "arbitral_proceeding",
    "civil_decree",
    "trial_judgment",
    "acquittal",
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
    evidence_quote_verbatim: bool | None = Field(
        default=None,
        description=(
            "Deterministic grounding stamp: True when evidence_quote is a "
            "normalized substring of the source text the enricher saw, False "
            "when it is a paraphrase/fabrication, None when no quote was "
            "claimed. Stamped at write time; never set by the LLM."
        ),
    )
    observables: list[CaseObservable] = Field(default_factory=list)


class ToolMention(BaseModel):
    """One named product/service and its role in the case (v3).

    Only products the SOURCE names — never inferred from behavior. Roles per
    TOOL_MENTION_ROLES; names feed the tooling catalog's candidate mining.
    """

    name: str
    role: Literal["caught", "bypassed", "misused", "traced"]
    evidence: str = Field(
        default="", description="Short phrase from the source supporting the role call"
    )


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
    schema_version: int = Field(
        default=1,
        description="Stored-field clamp generation; < ENRICH_SCHEMA_VERSION ⇒ re-enrich candidate",
    )
    # Case facts (feed the analyst note / legacy CaseRecord) — all default-safe.
    is_insider_case: bool = False
    # For non-cases only: what the piece is FOR, in ITM control language where
    # it fits — detection|prevention|tradecraft|policy|news. "" = unclassified
    # (pre-2026-08 enrichments; the UI falls back to a channel-based label).
    context_kind: str = ""
    actor_role: str | None = None
    access_vector: str | None = None
    motive_signals: list[str] = Field(default_factory=list)
    exfil_channels: list[str] = Field(default_factory=list)
    # v3 case facts. Citizenship only from an EXPLICIT statement in the source
    # — a name is never evidence of nationality; civil filings usually plead
    # only state citizenship (record "US (state pleaded)" for those).
    actor_citizenship: str | None = None
    industry: str = Field(default="unknown", description="Victim org sector (INDUSTRIES)")
    # ADDITIVE at v3 (2026-09-04, docs/schema-freeze-v4.md): the sector of the
    # organization that EMPLOYED the insider — diverges from ``industry`` for
    # tippees, contractors, and law-firm/advisor insiders. Null-preserving on
    # purpose: silence is not "unknown", so an older generation that never
    # asked the question stays None and the overlay rule can fill it.
    actor_employer_sector: str | None = Field(
        default=None,
        description=(
            "Sector of the insider's own employer, explicit statements only "
            "(INDUSTRIES enum); None when the text does not say — silence is "
            "not 'unknown'"
        ),
    )
    # Provenance of an overlaid additive value ({"model", "extracted_at"}) —
    # set ONLY by project_additive_fields when the value came from a
    # generation other than the selected one; never written by the LLM.
    actor_employer_sector_source: dict | None = None
    tool_mentions: list[ToolMention] = Field(default_factory=list)
    timeframe: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    extracted_at: datetime | None = None
    model: str | None = None


class EnrichmentRecord(BaseModel):
    """One stored enrichment generation of an article — never rewritten.

    An article accumulates an append-only list of these (see
    ``ProcessedArticle.enrichment_history``): one per enrichment run that
    actually produced output (a re-enrich on the current model, a widened
    clamp, a richer source body). ``forensics`` carries its own ``model`` /
    ``extracted_at`` / ``schema_version``; ``ai_summary`` is the analyst note
    that ran alongside it (the field the old single-slot store could gut on a
    thin re-run). ``ProcessedArticle.forensics`` / ``ai_summary`` are the
    *selected* view over this history (:func:`select_best_enrichment`) — this
    is the durable record.
    """

    ai_summary: str | None = None
    forensics: PerCaseForensics

    @property
    def model(self) -> str:
        return (self.forensics.model or "").strip()

    @property
    def schema_version(self) -> int:
        try:
            return int(self.forensics.schema_version or 1)
        except (TypeError, ValueError):
            return 1


def enrichment_richness(rec: EnrichmentRecord | None) -> float:
    """Analyst value of a generation — higher wins selection.

    Mirrors the recovery merge's scoring: an analyst note dominates, then the
    method count, then forensic confidence. A gutted generation (no note,
    methods=0) scores ~0; a full record scores well above it. Centralized here
    so the graph's select-best and the re-enrich drain rank generations the
    same way.
    """
    if rec is None:
        return 0.0
    note = 100.0 if (rec.ai_summary or "").strip() else 0.0
    methods = len(rec.forensics.methods or [])
    try:
        conf = float(rec.forensics.confidence or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    # An adjudicated outcome is analyst value the method count can't see: a
    # docket-follow re-enrichment that learns the case ended must not lose
    # selection to the complaint-stage record over one fewer method. +15
    # outranks a one-method deficit; a gutted record still can't win.
    outcome = 15.0 if (rec.forensics.outcome or "").strip() else 0.0
    return note + methods * 10.0 + conf + outcome


def _selection_key(rec: EnrichmentRecord) -> tuple[float, float]:
    extracted = rec.forensics.extracted_at
    recency = extracted.timestamp() if extracted is not None else 0.0
    return (enrichment_richness(rec), recency)


def _confidence(rec: EnrichmentRecord) -> float:
    try:
        return float(rec.forensics.confidence or 0.0)
    except (TypeError, ValueError):
        return 0.0


def select_best_enrichment(history: list[EnrichmentRecord]) -> EnrichmentRecord | None:
    """Pick the projected generation: richest WITHIN the winning verdict,
    considering only the newest SCHEMA TIER present.

    Schema tier first (2026-08-22, the Bruce v. Intuit lesson): a schema bump
    is a deliberate contract upgrade (docs/schema-freeze-v3.md) — its
    generations carry calibrated confidence bands and demanded fields, so
    comparing them against an older tier is meaningless in both directions: a
    v2 complaint stamped 0.95 must not outvote a calibrated v3 at 0.75, and
    v2 verbosity must not out-richness v3 tightness. Without this, a sweep
    archives better records that never project. Only generations at the
    highest ``schema_version`` in the history compete; histories with a
    single tier (all-legacy rows) behave exactly as before.

    Within the tier, richness alone must never flip ``is_insider_case``
    (2026-08-16 audit: method count dominates confidence 10x in
    ``enrichment_richness``, so a verbose low-confidence generation could
    silently re-adjudicate a case — fatal once multi-model sweeps write
    second opinions into history). Selection is two-stage: the VERDICT is won
    by whichever class's best generation carries the higher confidence (ties:
    richer, then newer); the projection is then the richest generation within
    that class. A thin re-enrich still can't gut a rich record, and a chatty
    one can't flip a confident adjudication.
    """
    if not history:
        return None
    top_tier = max(rec.schema_version for rec in history)
    best_by_verdict: dict[bool, EnrichmentRecord] = {}
    for rec in history:
        if rec.schema_version != top_tier:
            continue
        verdict = bool(rec.forensics.is_insider_case)
        incumbent = best_by_verdict.get(verdict)
        if incumbent is None or _selection_key(rec) > _selection_key(incumbent):
            best_by_verdict[verdict] = rec
    if not best_by_verdict:
        return None
    if len(best_by_verdict) == 1:
        return next(iter(best_by_verdict.values()))
    return max(best_by_verdict.values(), key=lambda r: (_confidence(r), _selection_key(r)))


# Fields added to the contract WITHOUT a schema bump (docs/schema-freeze-v4.md
# candidates ledger). A bump re-tiers every filing through the reenrich lane
# and churns verdicts for weeks; an additive field instead rides the overlay
# rule below: the selected projection fills a None additive value from the
# newest same-tier generation that carries one. Additive fields are NEVER a
# verdict or richness input — select-best is byte-identical with or without
# the overlay.
ADDITIVE_FIELDS: tuple[str, ...] = ("actor_employer_sector",)


def _donor_recency_key(indexed: tuple[int, EnrichmentRecord]) -> tuple[float, int]:
    """Newest extracted_at wins; ties (or None stamps) resolve to the LATER
    history position, so the pick is deterministic across calls and files."""
    idx, rec = indexed
    return (_selection_key(rec)[1], idx)


def project_additive_fields(
    history: list[EnrichmentRecord], best: EnrichmentRecord | None
) -> PerCaseForensics | None:
    """Overlay ADDITIVE_FIELDS onto the selected projection's forensics.

    For each additive field that is None on ``best``, take the value from the
    NEWEST generation at the top schema tier where it is non-null — preferring
    donors that share ``best``'s ``is_insider_case`` verdict, falling back to
    any same-tier donor — and stamp ``<field>_source = {"model",
    "extracted_at"}`` from that donor so the provenance is visible. The overlay
    touches nothing else — not ``is_insider_case``, ``confidence``,
    ``methods``, ``ai_summary`` or any richness input — and is a no-op
    (returns ``best.forensics`` itself) when no generation carries the field.
    ``best`` and history are never mutated: the return value is a copy.

    ``best`` must sit at history's top tier (it always does when it came from
    :func:`select_best_enrichment`; :func:`project_from_history` guarantees
    it). Anything else is a caller bug, not a data condition — raise.
    """
    if best is None:
        return None
    top_tier = max((rec.schema_version for rec in history), default=best.schema_version)
    if best.schema_version != top_tier:
        raise ValueError(
            f"project_additive_fields: best is at schema tier {best.schema_version} "
            f"but history's top tier is {top_tier} — project via project_from_history"
        )
    forensics = best.forensics
    verdict = bool(forensics.is_insider_case)
    update: dict[str, object] = {}
    for field in ADDITIVE_FIELDS:
        if getattr(forensics, field, None) is not None:
            continue
        donors = [
            (idx, rec)
            for idx, rec in enumerate(history)
            if rec is not best
            and rec.schema_version == top_tier
            and getattr(rec.forensics, field, None) is not None
        ]
        if not donors:
            continue
        same_verdict = [d for d in donors if bool(d[1].forensics.is_insider_case) == verdict]
        _idx, donor = max(same_verdict or donors, key=_donor_recency_key)
        update[field] = getattr(donor.forensics, field)
        extracted = donor.forensics.extracted_at
        update[f"{field}_source"] = {
            "model": donor.model or None,
            "extracted_at": extracted.isoformat() if extracted is not None else None,
        }
    if not update:
        return forensics
    return forensics.model_copy(update=update)


class Projection(NamedTuple):
    """The stored top-level view of an article's enrichment history."""

    best: EnrichmentRecord | None
    forensics: PerCaseForensics | None
    ai_summary: str | None
    case_record: CaseRecord | None


def project_from_history(history: list[EnrichmentRecord]) -> Projection:
    """THE one projection: select-best, then the additive-field overlay.

    Every site that writes ``ProcessedArticle.forensics`` / ``ai_summary`` /
    ``case_record`` from a history must go through here — the graph node's
    ``_emit_selected`` and the backfill sweep both do. Writing a freshly
    produced generation straight to the row (what the sweep did before
    2026-09-04) bypasses select-best, so a thin or verdict-flipped re-enrich
    gutted a rich record until the next graph pass re-selected it. The
    ``case_record`` is derived from the overlaid forensics exactly as the
    enricher derives it — one source of truth.
    """
    best = select_best_enrichment(history)
    forensics = project_additive_fields(history, best)
    record = case_record_from_forensics(forensics) if forensics is not None else None
    return Projection(
        best=best,
        forensics=forensics,
        ai_summary=best.ai_summary if best is not None else None,
        case_record=record,
    )


_QUOTE_NORM_TABLE = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-"})


def _normalize_quote_text(text: str) -> str:
    return " ".join((text or "").translate(_QUOTE_NORM_TABLE).lower().split())


def stamp_quote_verbatim(forensics: PerCaseForensics, source_text: str) -> PerCaseForensics:
    """Stamp each method's ``evidence_quote_verbatim`` against the source text.

    Deterministic and zero-LLM (audit item D5): a normalized substring check —
    case, whitespace runs, and typographic quote/dash variants are forgiven;
    anything else is a paraphrase or fabrication and stamps False. Empty
    quotes stay None: nothing claimed, nothing to verify. Runs at write time
    so every new generation carries its grounding verdict; a backfill script
    stamps stored history (scripts/backfill_quote_verbatim.py).
    """
    haystack = _normalize_quote_text(source_text)
    for method in forensics.methods or []:
        quote = (method.evidence_quote or "").strip()
        method.evidence_quote_verbatim = (
            None if not quote else _normalize_quote_text(quote) in haystack
        )
    return forensics


def _enrichment_signature(rec: EnrichmentRecord) -> tuple:
    """Identity of a generation for dedup — same signature ⇒ same output."""
    return (
        rec.model,
        rec.schema_version,
        (rec.ai_summary or "").strip(),
        len(rec.forensics.methods or []),
        round(float(rec.forensics.confidence or 0.0), 4),
    )


def append_enrichment(
    history: list[EnrichmentRecord], new: EnrichmentRecord
) -> list[EnrichmentRecord]:
    """Return history with ``new`` appended, unless an identical run exists.

    Append-only: existing generations are never mutated or dropped. A re-run of
    the same model+schema that reproduces the same output (signature) is
    skipped so history doesn't bloat with duplicates, honoring "keep all, dedup
    per model." A genuinely different generation always lands.
    """
    signatures = {_enrichment_signature(rec) for rec in history}
    if _enrichment_signature(new) in signatures:
        return list(history)
    return [*history, new]


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
        action = _s(raw.get("action"), 600)
        if not action:
            continue
        claim = str(raw.get("claim_status") or "").strip().lower()
        methods.append(
            CaseMethod(
                action=action,
                tools=_slist(raw.get("tools"), 6, 80),
                target_data=_s(raw.get("target_data"), 300) or None,
                quantity=_s(raw.get("quantity"), 100) or None,
                claim_status=claim if claim in CLAIM_STATUSES else "unclear",
                evidence_quote=_s(raw.get("evidence_quote"), 600),
                observables=parse_observables(raw.get("observables")),
            )
        )
    return methods


def parse_tool_mentions(raw: object) -> list[ToolMention]:
    """Lenient v3 tool-mention coercion — bad entries drop, never raise."""
    out: list[ToolMention] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:12]:
        if not isinstance(item, dict):
            continue
        name = _s(item.get("name"), 120)
        role = str(item.get("role") or "").strip().lower()
        if not name or role not in TOOL_MENTION_ROLES:
            continue
        out.append(ToolMention(name=name, role=role, evidence=_s(item.get("evidence"), 300)))
    return out


def coerce_additive_enum(value: object) -> str | None:
    """INDUSTRIES member or None — never "unknown" (silence is not an answer)."""
    v = str(value or "").strip().lower()
    return v if v in INDUSTRIES and v != "unknown" else None


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
        actor_profile=_s(data.get("actor_profile"), 500),
        timeline=_slist(data.get("timeline"), 10, 400),
        methods=_coerce_methods(data.get("methods")),
        # Full-sentence narrative fields — storage safety bound only, so the UI's
        # DETECTED VIA / OUTCOME persist whole and expand rather than clip
        # mid-sentence (matches the CaseRecord narrative clamp, _CASE_TEXT_MAX_CHARS).
        detection=_s(data.get("detection"), 2000) or None,
        outcome=_s(data.get("outcome"), 2000) or None,
        hunt_terms=_slist(data.get("hunt_terms"), 12, 120),
        # v3: hunt_queries dropped from the enricher contract (dead weight —
        # operator call). The field itself stays on the model so stored v1/v2
        # records round-trip; nothing new is written into it.
        hunt_queries=[],
        is_insider_case=bool(data.get("is_insider_case")),
        context_kind=(lambda v: v if v in CONTEXT_KINDS else "")(
            str(data.get("context_kind") or "").strip().lower()
        ),
        actor_role=_s(data.get("actor_role"), 200) or None,
        access_vector=_s(data.get("access_vector"), 200) or None,
        motive_signals=_slist(data.get("motive_signals"), 8, 200),
        exfil_channels=_slist(data.get("exfil_channels"), 8, 200),
        actor_citizenship=_s(data.get("actor_citizenship"), 200) or None,
        industry=(lambda v: v if v in INDUSTRIES else "unknown")(
            str(data.get("industry") or "").strip().lower()
        ),
        # Null-preserving (unlike industry): off-enum, missing, OR "unknown" →
        # None. "unknown" is in INDUSTRIES for the victim sector's clamp, but
        # stored non-null here it would block the field forever (the backfill
        # skips non-null rows and the overlay would treat it as a real answer).
        actor_employer_sector=coerce_additive_enum(data.get("actor_employer_sector")),
        tool_mentions=parse_tool_mentions(data.get("tool_mentions")),
        timeframe=_s(data.get("timeframe"), 200) or None,
        confidence=max(0.0, min(1.0, confidence)),
        extraction_status="llm",
        schema_version=ENRICH_SCHEMA_VERSION,
    )
