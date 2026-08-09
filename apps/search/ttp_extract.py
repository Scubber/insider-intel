"""Extraction-board case study: assembled from stored ingest-time forensics.

Every qualifying article is enriched once at ingest
(``shared/agents/summarize.py``) into a ``PerCaseForensics`` record; this
module assembles the boarded records into a technique-centric forensic
readout in code — no LLM at read time. Per technique: how each case did it
(from the stored method actions), the forensic observables that behavior
leaves in a defender's environment, and ITM detections/preventions attached
from the catalog. Articles not yet enriched fall back to
``forensics_from_floor`` (their ITM/case-record data), so a report is never
empty — it just gets richer as the corpus is enriched.

Hunting guidance deliberately does NOT live here: the case-literal query
seeds this report used to carry moved to the technique dossier's synthesized,
tool-agnostic patterns (``apps/aggregator/hunt_synthesis.py``).
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from apps.search.index import ArticleSearchIndex
from shared.schemas import ProcessedArticle
from shared.schemas.articles import ControlRef
from shared.schemas.forensics import CaseMethod, CaseObservable, PerCaseForensics
from shared.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# Keep aligned with web/app.js IF038_TTP_SEEDS / docs/ttps_overemployment.md
IF038_TTP_SEEDS: list[dict[str, Any]] = [
    {
        "id": "TTP-OE-01",
        "behavior": "Undisclosed second full-time remote job (dual employment / overemployment).",
    },
    {
        "id": "TTP-OE-02",
        "behavior": "Competitor / customer side work (trade-secret adjacent concurrent role).",
    },
    {
        "id": "TTP-OE-03",
        "behavior": "Time fraud: overlapping billable hours across employers.",
    },
    {
        "id": "TTP-OE-04",
        "behavior": "Comms diversion to personal channels to hide the second role.",
    },
    {
        "id": "TTP-OE-05",
        "behavior": "Policy evasion: concealed conflict-of-interest / outside-work disclosure.",
    },
]


class ExtractTtpsRequest(BaseModel):
    links: list[str] = Field(default_factory=list, min_length=1, max_length=40)


class TtpBehavior(BaseModel):
    id: str
    text: str


class TechniqueDetectionGuidance(BaseModel):
    """How to detect this technique: ITM catalog controls (tool-agnostic)."""

    detections: list[ControlRef] = Field(default_factory=list)
    preventions: list[ControlRef] = Field(default_factory=list)


class TtpCaseEvidence(BaseModel):
    """One board case's observed behaviors under a technique section."""

    title: str
    link: str = ""
    bullets: list[str] = Field(default_factory=list)
    tradecraft: str = ""
    legal_posture: str = ""


class TtpTechniqueSection(BaseModel):
    """Per-ITM-ID report section: what the technique is + how each case did it."""

    id: str
    title: str = ""
    description: str = ""
    cases: list[TtpCaseEvidence] = Field(default_factory=list)
    theme: str = ""
    tradecraft_summary: str = ""
    observables: list[CaseObservable] = Field(default_factory=list)
    detection: TechniqueDetectionGuidance = Field(default_factory=TechniqueDetectionGuidance)


class ExtractTtpsResponse(BaseModel):
    mode: Literal["llm", "seeds"]
    article_count: int
    titles: list[str] = Field(default_factory=list)
    summary: str = ""
    techniques: list[TtpTechniqueSection] = Field(default_factory=list)
    behaviors: list[TtpBehavior] = Field(default_factory=list)
    matched_if038: bool = False
    detail: str = ""
    report_version: int = 1


def _uniq(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        cleaned = str(raw or "").strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _technique_lookup(tech_id: str):
    """The full ItmTechnique for an id (case-insensitive), or None."""
    from shared.itm.index import load_itm_index

    for tech in load_itm_index().techniques:
        if tech.id.upper() == tech_id.upper():
            return tech
    return None


def _technique_meta(tech_id: str) -> tuple[str, str] | None:
    """(title, first-sentence description) from the ITM catalog, or None."""
    tech = _technique_lookup(tech_id)
    if tech is None:
        return None
    desc = (tech.description_text or "").strip()
    if desc:
        first = desc.split(". ", 1)[0].strip()
        desc = (first if first.endswith(".") else first + ".")[:220]
    return tech.title, desc


def _attach_controls(section: TtpTechniqueSection) -> None:
    """DT*/PV* come from the catalog in code — never from the LLM."""
    tech = _technique_lookup(section.id)
    if tech is None:
        return
    section.theme = section.theme or tech.theme
    section.detection.detections = sorted(
        {ref.id: ControlRef(id=ref.id, title=ref.title) for ref in tech.detections}.values(),
        key=lambda c: c.id,
    )
    section.detection.preventions = sorted(
        {ref.id: ControlRef(id=ref.id, title=ref.title) for ref in tech.preventions}.values(),
        key=lambda c: c.id,
    )


def _technique_behavior_text(tech_id: str, fallback_title: str) -> str:
    """First sentence of the catalog description — a behavior, not a label."""
    meta = _technique_meta(tech_id)
    if meta is None:
        return fallback_title
    title, desc = meta
    return desc or title


def seed_floor_report(
    articles: list[ProcessedArticle],
    *,
    detail: str = "",
) -> ExtractTtpsResponse:
    """Evidence-first seed report.

    Behaviors come from what the selection actually shows — its ITM hits and
    case-record methods. The hardcoded IF038 overemployment pack is only
    emitted when IF038 itself matched, or as an explicitly-labeled generic
    fallback when the selection carries no evidence at all.
    """
    behaviors: list[TtpBehavior] = []
    sections: dict[str, TtpTechniqueSection] = {}

    seen_tech: set[str] = set()
    case_methods: list[str] = []
    for article in articles:
        record = getattr(article, "case_record", None)
        case_bullets: list[str] = []
        if record is not None:
            case_bullets.extend(record.methods)
            case_bullets.extend(f"Exfil channel: {c}" for c in record.exfil_channels)
            if record.detection_trigger:
                case_bullets.append(f"Detected via: {record.detection_trigger}")
        for hit in article.entities.itm_hits or []:
            tid = str(hit.id).upper()
            if tid not in seen_tech:
                seen_tech.add(tid)
                behaviors.append(
                    TtpBehavior(id=hit.id, text=_technique_behavior_text(hit.id, hit.title))
                )
            section = sections.get(tid)
            if section is None:
                section = TtpTechniqueSection(
                    id=tid,
                    title=hit.title,
                    description=_technique_behavior_text(hit.id, hit.title),
                )
                sections[tid] = section
            if not any(c.link == article.link for c in section.cases):
                bullets = list(case_bullets)
                aliases = _uniq(list(hit.matched_aliases or []))[:6]
                if aliases:
                    bullets.append(f"Matched in text: {', '.join(aliases)}")
                section.cases.append(
                    TtpCaseEvidence(title=article.title, link=article.link, bullets=bullets)
                )
        if record is not None:
            case_methods.extend(record.methods)

    unique_methods = _uniq(case_methods)
    for n, method in enumerate(unique_methods, start=1):
        behaviors.append(TtpBehavior(id=f"CASE-{n:02d}", text=f"Case-observed method: {method}"))

    summary = ""
    if sections:
        ids = ", ".join(sections.keys())
        summary = f"{len(articles)} board case(s) show {len(sections)} ITM technique(s): {ids}."
        if unique_methods:
            summary += f" {len(unique_methods)} case-observed method(s) on record."

    matched = any(
        any(str(h.id).upper() == "IF038" for h in (a.entities.itm_hits or [])) for a in articles
    )
    if matched or not behaviors:
        # IF038 matched → its pack belongs in the report. No evidence at all →
        # the pack is a last-resort floor, labeled honestly below.
        for ttp in IF038_TTP_SEEDS:
            behaviors.append(TtpBehavior(id=ttp["id"], text=ttp["behavior"]))
        if not matched and not detail:
            detail = "Generic overemployment pack — no matched evidence in selection"

    if not detail:
        # Honest labeling: this is real board evidence, not a canned seed pack.
        detail = f"Evidence pack · {len(sections)} technique(s)"
        if unique_methods:
            detail += f" · {len(unique_methods)} case method(s)"

    return ExtractTtpsResponse(
        mode="seeds",
        article_count=len(articles),
        titles=[a.title for a in articles],
        summary=summary,
        techniques=list(sections.values()),
        behaviors=behaviors,
        matched_if038=matched,
        detail=detail,
    )


def forensics_from_floor(article: ProcessedArticle) -> PerCaseForensics:
    """Reshape ingest-time CaseRecord/ITM data into a forensic record.

    The fallback for articles the ingest enricher has not reached yet: the
    report still shows their case-record methods and matched techniques, just
    without the deeper reconstruction. ``extraction_status="floor"`` marks it.
    """
    record = getattr(article, "case_record", None)
    methods: list[CaseMethod] = []
    actor_bits: list[str] = []
    detection: str | None = None
    outcome: str | None = None
    if record is not None:
        actor_bits = [b for b in (record.actor_role, record.access_vector) if b]
        detection = record.detection_trigger
        outcome = record.outcome
        for method in record.methods:
            methods.append(CaseMethod(action=method))
        for channel in record.exfil_channels:
            methods.append(
                CaseMethod(
                    action=f"Exfiltration via {channel}",
                    observables=[
                        CaseObservable(
                            description=f"Transfers to {channel}",
                            artifact="egress/network logs",
                            channel="network",
                        )
                    ],
                )
            )
    return PerCaseForensics(
        link=article.link,
        title=article.title,
        actor_profile=" — ".join(actor_bits),
        methods=methods,
        detection=detection,
        outcome=outcome,
        candidate_technique_ids=[str(h.id).upper() for h in article.entities.itm_hits or []],
        hunt_terms=list(article.entities.operator_terms or []),
        extraction_status="floor",
    )


def _mechanical_sections(
    floor: ExtractTtpsResponse, forensics: list[PerCaseForensics]
) -> ExtractTtpsResponse:
    """Build technique sections from enriched forensic records, over the floor.

    Bullets come straight from the stored method actions; observables aggregate
    per technique. Floor-only records (not yet enriched) are skipped here — the
    lexical floor already carries their sections — so the report degrades
    gracefully as coverage fills in.
    """
    report = floor.model_copy(deep=True)
    merged = {s.id: s for s in report.techniques}
    for record in forensics:
        if record.extraction_status != "llm":
            continue
        bullets = [m.action for m in record.methods][:8]
        observables = [o for m in record.methods for o in m.observables]
        for tid in record.candidate_technique_ids:
            meta = _technique_meta(tid)
            if meta is None:
                continue
            title, description = meta
            section = merged.get(tid)
            if section is None:
                section = TtpTechniqueSection(id=tid, title=title, description=description)
                merged[tid] = section
            if not any(c.link == record.link for c in section.cases):
                posture = getattr(record, "legal_posture", "") or ""
                section.cases.append(
                    TtpCaseEvidence(
                        title=record.title,
                        link=record.link,
                        bullets=bullets,
                        legal_posture="" if posture == "unknown" else posture,
                    )
                )
            seen = {(o.description.lower(), o.channel) for o in section.observables}
            for obs in observables:
                key = (obs.description.lower(), obs.channel)
                if key not in seen and len(section.observables) < 8:
                    seen.add(key)
                    section.observables.append(obs)
    report.techniques = list(merged.values())[:12]
    return report


def extract_ttps_for_links(
    index: ArticleSearchIndex,
    links: list[str],
    *,
    settings: Settings | None = None,
) -> ExtractTtpsResponse:
    """Assemble a forensic case study from stored ingest-time forensic records.

    No LLM at read time: each boarded article contributes its stored
    ``forensics`` record (or a floor-derived one if not yet enriched), and the
    report is built in code — technique sections with per-case tradecraft and
    observables, plus ITM detections/preventions from the catalog. Hunting
    guidance lives in the technique dossier's synthesized patterns, not here.
    """
    _ = settings or get_settings()  # reserved for future knobs; kept for signature parity
    articles: list[ProcessedArticle] = []
    missing: list[str] = []
    for link in links:
        article = index.get_by_link(link)
        if article is None:
            missing.append(link)
        else:
            articles.append(article)

    if not articles:
        return ExtractTtpsResponse(
            mode="seeds",
            article_count=0,
            titles=[],
            detail="No indexed articles matched board links",
        )

    floor = seed_floor_report(articles)
    if missing:
        floor.detail = f"{floor.detail}; {len(missing)} link(s) not in index"

    forensics = [
        (
            article.forensics.model_copy(update={"link": article.link, "title": article.title})
            if getattr(article, "forensics", None) is not None
            else forensics_from_floor(article)
        )
        for article in articles
    ]
    enriched = sum(1 for r in forensics if r.extraction_status == "llm")
    floor_count = len(forensics) - enriched

    report = _mechanical_sections(floor, forensics)
    for section in report.techniques:
        _attach_controls(section)

    report.mode = "llm" if enriched else "seeds"
    report.report_version = 4
    report.detail = (
        f"Assembled from stored forensics · {enriched} enriched / {floor_count} floor source(s)"
    )
    return report
