"""Extraction-board TTP hunt report: evidence floor + two-stage LLM extraction.

The floor is built from what the board actually shows (ITM hits, case
records). On top of it, stage 1 (``apps.search.deep_extract``) deep-reads each
board article into a forensic reconstruction, and stage 2 synthesizes those
into a technique-centric report: per-technique tradecraft, the observables
that behavior leaves in a defender's environment, and case-grounded hunt
queries. ITM detections/preventions are attached from the catalog in code —
the LLM only writes what code can't. Every LLM failure degrades one rung down
the ladder (floor-derived forensics → mechanical sections → pure floor).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from apps.search import deep_extract
from apps.search.deep_extract import (
    STAGE2_MAX_TOKENS,
    STAGE2_SYSTEM_PROMPT,
    CaseObservable,
    PerCaseForensics,
    build_stage2_user_prompt,
    forensics_from_floor,
    parse_observables,
    select_deep_articles,
)
from apps.search.index import ArticleSearchIndex
from shared.schemas import ProcessedArticle
from shared.schemas.articles import ControlRef, resolve_channel
from shared.settings import Settings, get_settings

logger = logging.getLogger(__name__)

XAI_CHAT_URL = "https://api.x.ai/v1/chat/completions"
COURTLISTENER_SEARCH_URL = "https://www.courtlistener.com/api/rest/v4/search/"

# Keep aligned with web/app.js IF038_TTP_SEEDS / docs/ttps_overemployment.md
IF038_TTP_SEEDS: list[dict[str, Any]] = [
    {
        "id": "TTP-OE-01",
        "behavior": "Undisclosed second full-time remote job (dual employment / overemployment).",
        "email": [
            "personal-domain mail during work hours",
            "Job B recruiter/HR threads",
            "personal calendar invites for Job B standups",
        ],
        "chat": [
            "second Slack/Teams identity",
            "J2 / OE / overemployed language",
            "status always Busy/BRB",
        ],
        "network": [
            "concurrent SaaS sessions for different orgs",
            "personal VPN + corp VPN patterns",
            "after-hours bursty productivity tools",
        ],
        "human": [
            "missing/false outside-employment or COI disclosure",
            "dual W-2 / multiple employers on tax or benefits",
            "LinkedIn current roles vs HRIS title mismatch",
        ],
        "seeds": [
            "outside employment",
            "moonlighting",
            "J2",
            "overemployed",
            "second job",
            "dual employment",
            "conflict of interest disclosure",
        ],
    },
    {
        "id": "TTP-OE-02",
        "behavior": "Competitor / customer side work (trade-secret adjacent concurrent role).",
        "email": [
            "competitor-domain threads",
            "side project share of internal decks",
            "personal Dropbox/Drive links in corp mail",
        ],
        "chat": [
            "screenshots of internal tools",
            "my other company",
            "recruiting coworkers",
        ],
        "network": [
            "large personal-cloud uploads",
            "USB/email exfil near resignation",
            "repos unused in day job",
        ],
        "human": [
            "undisclosed advisory/contractor role",
            "COI form none",
            "resignation timed with competitor start",
        ],
        "seeds": [
            "competitor",
            "side project",
            "advisory",
            "consulting agreement",
            "DTSA",
            "trade secret",
            "customer list",
        ],
    },
    {
        "id": "TTP-OE-03",
        "behavior": "Using Employer A time/tools for Employer B.",
        "email": [
            "drafts to Job B from corp mailbox",
            "vague calendar blocks with no corp attendees",
        ],
        "chat": [
            "Job B tickets pasted into corp chat",
            "second browser profile language",
        ],
        "network": [
            "Job B IdP on corp device",
            "RDP/VDI to personal systems",
            "clipboard/file activity to personal cloud",
        ],
        "human": [
            "timekeeping anomalies",
            "always in meetings without corp artifacts",
            "PIP for availability",
        ],
        "seeds": ["personal laptop", "my other job", "client call"],
    },
    {
        "id": "TTP-OE-04",
        "behavior": "Identity split — personal stack for Job B, corp stack for Job A.",
        "email": ["auto-forward corp to personal", "Job B never on corp systems"],
        "chat": ["text me on my personal", "Signal/WhatsApp for work topics"],
        "network": ["MDM gaps", "personal hotspot only", "corp VPN idle while claiming hours"],
        "human": [
            "unreachable on corp mobile",
            "refuses MDM on personal devices used for work",
        ],
        "seeds": ["personal phone", "text me", "Signal", "WhatsApp", "forward to Gmail"],
    },
    {
        "id": "TTP-OE-05",
        "behavior": "False or incomplete outside-employment / COI disclosure.",
        "email": [
            "outside employment policy signature threads unanswered",
            "policy reminders ignored",
        ],
        "chat": ["don't tell HR", "policy screenshot shares"],
        "network": ["pair with HRIS — low network signal alone"],
        "human": [
            "form answers vs LinkedIn/tax/benefits",
            "AP payments to employee LLC",
            "1099s",
        ],
        "seeds": [
            "outside employment policy",
            "conflict of interest form",
            "disclosure form",
            "moonlighting policy",
        ],
    },
]


class ExtractTtpsRequest(BaseModel):
    links: list[str] = Field(default_factory=list, min_length=1, max_length=40)


class TtpBehavior(BaseModel):
    id: str
    text: str


class HuntQuery(BaseModel):
    """Case-grounded hunt logic an analyst can adapt — not a keyword chip."""

    stack: str
    logic: str
    rationale: str = ""


class TechniqueDetectionGuidance(BaseModel):
    """How to detect this technique: ITM controls + LLM-written hunt logic."""

    detections: list[ControlRef] = Field(default_factory=list)
    preventions: list[ControlRef] = Field(default_factory=list)
    hunt_queries: list[HuntQuery] = Field(default_factory=list)


class TtpCaseEvidence(BaseModel):
    """One board case's observed behaviors under a technique section."""

    title: str
    link: str = ""
    bullets: list[str] = Field(default_factory=list)
    tradecraft: str = ""


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
    email: list[str] = Field(default_factory=list)
    chat: list[str] = Field(default_factory=list)
    network: list[str] = Field(default_factory=list)
    human: list[str] = Field(default_factory=list)
    seeds: list[str] = Field(default_factory=list)
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
    email: list[str] = []
    chat: list[str] = []
    network: list[str] = []
    human: list[str] = []
    seeds: list[str] = []
    sections: dict[str, TtpTechniqueSection] = {}

    seen_tech: set[str] = set()
    case_methods: list[str] = []
    for article in articles:
        seeds.extend(article.entities.operator_terms or [])
        record = getattr(article, "case_record", None)
        case_bullets: list[str] = []
        if record is not None:
            case_bullets.extend(record.methods)
            case_bullets.extend(f"Exfil channel: {c}" for c in record.exfil_channels)
            if record.detection_trigger:
                case_bullets.append(f"Detected via: {record.detection_trigger}")
        for hit in article.entities.itm_hits or []:
            seeds.extend(hit.matched_aliases or [])
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
            seeds.extend(record.methods)
            seeds.extend(record.exfil_channels)
            network.extend(record.exfil_channels)
            if record.detection_trigger:
                human.append(record.detection_trigger)

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
            email.extend(ttp["email"])
            chat.extend(ttp["chat"])
            network.extend(ttp["network"])
            human.extend(ttp["human"])
            seeds.extend(ttp["seeds"])
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
        email=_uniq(email),
        chat=_uniq(chat),
        network=_uniq(network),
        human=_uniq(human),
        seeds=_uniq(seeds),
        matched_if038=matched,
        detail=detail,
    )


def enrich_courtlistener_snippet(
    article: ProcessedArticle,
    *,
    token: str | None,
    client: httpx.Client | None = None,
) -> str:
    """Best-effort free opinion/search snippet — never buys PACER PDFs."""
    channel = resolve_channel(article.source_id, getattr(article, "channel", None))
    if channel != "filings" and "courtlistener" not in (article.source_id or "").lower():
        return ""
    if not token or not token.strip():
        return ""

    query = (article.title or "").strip()
    if not query:
        return ""

    headers = {
        "Accept": "application/json",
        "Authorization": f"Token {token.strip()}",
    }
    params = {"q": query, "type": "o", "order_by": "score desc", "page_size": 2}

    owns_client = client is None
    http = client or httpx.Client(timeout=20.0)
    try:
        resp = http.get(COURTLISTENER_SEARCH_URL, headers=headers, params=params)
        if resp.status_code >= 400:
            logger.info("CourtListener enrich HTTP %s", resp.status_code)
            return ""
        payload = resp.json()
        results = payload.get("results") or []
        snippets: list[str] = []
        for hit in results[:2]:
            case_name = hit.get("caseName") or hit.get("caseNameFull") or ""
            snippet = (
                hit.get("snippet")
                or hit.get("meta")
                or hit.get("text")
                or hit.get("plain_text")
                or ""
            )
            if isinstance(snippet, str) and snippet.strip():
                snippets.append(f"{case_name}: {snippet.strip()}" if case_name else snippet.strip())
            elif case_name:
                snippets.append(str(case_name))
        return "\n".join(snippets)[:2000]
    except Exception as exc:  # noqa: BLE001 — enrich is best-effort
        logger.info("CourtListener enrich failed: %s", exc)
        return ""
    finally:
        if owns_client:
            http.close()


_LEGACY_CHANNEL_FIELD = {
    "email": "email",
    "chat": "chat",
    "network": "network",
    "cloud": "network",
    "identity": "network",
    "endpoint": "network",
    "human": "human",
    "physical": "human",
}


def _resolve_case_link(
    raw_link: str, raw_title: str, articles: list[ProcessedArticle]
) -> tuple[str, str]:
    """(link, title) resolved against the board — exact link first.

    Stage 2 receives links in its input so exact match is the norm; the
    title-token containment fallback survives for models that echo abbreviated
    case names ("DictateMD v. Ahmadi") instead.
    """
    for article in articles:
        if raw_link and article.link == raw_link:
            return article.link, article.title
    tokens = set(re.findall(r"[a-z0-9]+", raw_title.lower()))
    if tokens:
        for article in articles:
            board_tokens = set(re.findall(r"[a-z0-9]+", article.title.lower()))
            if board_tokens and (tokens <= board_tokens or board_tokens <= tokens):
                return article.link, article.title
    return raw_link, raw_title


def _parse_hunt_queries(value: Any) -> list[HuntQuery]:
    queries: list[HuntQuery] = []
    if not isinstance(value, list):
        return queries
    for raw in value[:5]:
        if not isinstance(raw, dict):
            continue
        logic = str(raw.get("logic") or "").strip()[:600]
        if not logic:
            continue
        queries.append(
            HuntQuery(
                stack=str(raw.get("stack") or "").strip()[:60] or "SIEM",
                logic=logic,
                rationale=str(raw.get("rationale") or "").strip()[:300],
            )
        )
    return queries


def _merge_synthesis(
    floor: ExtractTtpsResponse,
    synthesis: dict[str, Any],
    *,
    articles: list[ProcessedArticle],
) -> ExtractTtpsResponse:
    """Validated stage-2 sections layered over the floor.

    Only real ITM catalog ids survive; sections the LLM missed but the lexical
    floor found stay in the report so evidence never disappears.
    """
    report = floor.model_copy(deep=True)
    merged = {s.id: s for s in report.techniques}
    for raw in (synthesis.get("techniques") or [])[:24]:
        if not isinstance(raw, dict):
            continue
        tid = str(raw.get("id") or "").strip().upper()
        meta = _technique_meta(tid)
        if not tid or meta is None:
            continue
        title, description = meta
        cases: list[TtpCaseEvidence] = []
        for case in (raw.get("cases") or [])[:8]:
            if not isinstance(case, dict):
                continue
            link, case_title = _resolve_case_link(
                str(case.get("link") or "").strip(),
                str(case.get("title") or "").strip()[:200],
                articles,
            )
            bullets = [
                str(b).strip()[:300] for b in (case.get("bullets") or [])[:8] if str(b).strip()
            ]
            tradecraft = str(case.get("tradecraft") or "").strip()[:600]
            if not (bullets or tradecraft) or not (case_title or link):
                continue
            cases.append(
                TtpCaseEvidence(
                    title=case_title or link, link=link, bullets=bullets, tradecraft=tradecraft
                )
            )
        if not cases:
            continue
        merged[tid] = TtpTechniqueSection(
            id=tid,
            title=title,
            description=description,
            cases=cases,
            tradecraft_summary=str(raw.get("tradecraft_summary") or "").strip()[:800],
            observables=parse_observables(raw.get("observables"), limit=8),
            detection=TechniqueDetectionGuidance(
                hunt_queries=_parse_hunt_queries(raw.get("hunt_queries"))
            ),
        )
    report.techniques = list(merged.values())[:12]
    report.summary = str(synthesis.get("summary") or "").strip()[:900] or floor.summary
    return report


def _mechanical_sections(
    floor: ExtractTtpsResponse, forensics: list[PerCaseForensics]
) -> ExtractTtpsResponse:
    """Stage-2-failed fallback: group stage-1 records by their technique ids.

    No prose synthesis — bullets come straight from extracted method actions,
    observables aggregate per technique; floor sections are unioned in.
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
                section.cases.append(
                    TtpCaseEvidence(title=record.title, link=record.link, bullets=bullets)
                )
            seen = {(o.description.lower(), o.channel) for o in section.observables}
            for obs in observables:
                key = (obs.description.lower(), obs.channel)
                if key not in seen and len(section.observables) < 8:
                    seen.add(key)
                    section.observables.append(obs)
    report.techniques = list(merged.values())[:12]
    return report


def _derive_legacy_fields(report: ExtractTtpsResponse, forensics: list[PerCaseForensics]) -> None:
    """Fill email/chat/network/human/seeds from the new structure.

    Keeps the plaintext export, the copy-LLM-prompt, the generic hunt-query
    templates, and the offline seed-pack path working with zero shape change.
    """
    buckets: dict[str, list[str]] = {"email": [], "chat": [], "network": [], "human": []}
    observables = [o for s in report.techniques for o in s.observables]
    observables.extend(o for r in forensics for m in r.methods for o in m.observables)
    for obs in observables:
        field = _LEGACY_CHANNEL_FIELD.get(obs.channel, "network")
        cue = f"{obs.description} ({obs.artifact})" if obs.artifact else obs.description
        buckets[field].append(cue)
    report.email = _uniq(report.email + buckets["email"])
    report.chat = _uniq(report.chat + buckets["chat"])
    report.network = _uniq(report.network + buckets["network"])
    report.human = _uniq(report.human + buckets["human"])
    report.seeds = _uniq(report.seeds + [t for r in forensics for t in r.hunt_terms])


def _parse_llm_json(content: str) -> dict[str, Any] | None:
    cleaned = (content or "").strip()
    if not cleaned:
        return None
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", cleaned)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        data = json.loads(cleaned)
    except ValueError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        data = json.loads(match.group(0))
    return data if isinstance(data, dict) else None


def resolve_extract_providers(
    cfg: Settings, stage: Literal["stage1", "stage2"] | None = None
) -> list[tuple[str, str]]:
    """Ordered (provider, model) candidates for the extract LLM.

    An explicit provider choice yields exactly that provider (or nothing if
    its key is missing). "auto" lists every configured provider — the caller
    tries them in order, so a provider that errors at request time (e.g. an
    account out of credits) falls through to the next one.

    ``stage`` applies the EXTRACT_STAGE{1,2}_LLM_PROVIDER / _MODEL overrides;
    unset overrides inherit the base EXTRACT_LLM_PROVIDER behavior. A model
    override only makes sense with an explicit provider — under "auto" it is
    ignored (which provider it belongs to would be a guess).
    """
    from shared.llm import resolve_gemini_compat, resolve_openai_compat

    provider_override: str | None = None
    model_override: str | None = None
    if stage == "stage1":
        provider_override = cfg.extract_stage1_llm_provider
        model_override = cfg.extract_stage1_model
    elif stage == "stage2":
        provider_override = cfg.extract_stage2_llm_provider
        model_override = cfg.extract_stage2_model

    choice = (provider_override or cfg.extract_llm_provider or "auto").strip().lower()
    xai_key = (cfg.xai_api_key or "").strip()
    anthropic_key = (cfg.anthropic_api_key or "").strip()
    gemini_key = (cfg.gemini_api_key or "").strip()
    openai_key = (cfg.openai_api_key or "").strip()

    def with_model(default: str) -> str:
        return (model_override or "").strip() or default

    if choice == "none":
        return []
    if choice == "xai":
        return [("xai", with_model(cfg.xai_model))] if xai_key else []
    if choice == "anthropic":
        return [("anthropic", with_model(cfg.anthropic_model))] if anthropic_key else []
    if choice == "gemini":
        return [("gemini", with_model(cfg.gemini_model))] if gemini_key else []
    if choice == "openai":
        # Explicit choice allows keyless local endpoints (Ollama etc.).
        return [("openai", with_model(resolve_openai_compat(cfg)[1]))]
    if model_override:
        logger.warning("EXTRACT_%s_MODEL ignored: provider is 'auto'", (stage or "").upper())
    # auto: every configured key, in preference order. The localhost endpoint
    # is never auto-picked — only a real key signals intent.
    candidates: list[tuple[str, str]] = []
    if xai_key:
        candidates.append(("xai", cfg.xai_model))
    if anthropic_key:
        candidates.append(("anthropic", cfg.anthropic_model))
    if gemini_key:
        candidates.append(("gemini", resolve_gemini_compat(cfg)[1]))
    if openai_key:
        candidates.append(("openai", resolve_openai_compat(cfg)[1]))
    return candidates


def _call_extract_llm(
    *,
    provider: str,
    model: str,
    system: str,
    user: str,
    cfg: Settings,
    max_tokens: int = 2000,
) -> dict[str, Any] | None:
    if provider == "xai":
        headers = {
            "Authorization": f"Bearer {(cfg.xai_api_key or '').strip()}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        with httpx.Client(timeout=75.0) as client:
            resp = client.post(XAI_CHAT_URL, headers=headers, json=body)
            if resp.status_code >= 400:
                logger.warning("xAI extract HTTP %s: %s", resp.status_code, resp.text[:300])
                return None
            data = resp.json()
            content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            return _parse_llm_json(content)
    if provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic(api_key=(cfg.anthropic_api_key or "").strip(), timeout=75.0)
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts = [block.text for block in message.content if getattr(block, "text", None)]
        return _parse_llm_json("".join(parts))
    if provider in ("openai", "gemini"):
        from shared.llm import resolve_gemini_compat, resolve_openai_compat
        from shared.llm.openai_provider import _chat_completion

        resolver = resolve_gemini_compat if provider == "gemini" else resolve_openai_compat
        base_url, _default_model, api_key = resolver(cfg)
        content = _chat_completion(
            base_url=base_url.rstrip("/"),
            model=model,
            api_key=api_key,
            timeout=75.0,
            system=system,
            user=user,
            max_tokens=max_tokens,
        )
        return _parse_llm_json(content or "")
    logger.warning("Unknown extract provider %r", provider)
    return None


class _ProviderCaller:
    """Callable running the provider fall-through chain for one stage."""

    def __init__(self, candidates: list[tuple[str, str]], cfg: Settings) -> None:
        self.candidates = candidates
        self.cfg = cfg
        self.last_provider: str | None = None
        self.failures: list[str] = []

    def __call__(self, system: str, user: str, max_tokens: int) -> dict[str, Any] | None:
        for provider, model in self.candidates:
            try:
                data = _call_extract_llm(
                    provider=provider,
                    model=model,
                    system=system,
                    user=user,
                    cfg=self.cfg,
                    max_tokens=max_tokens,
                )
            except Exception as exc:  # noqa: BLE001 — fall through to the next provider
                logger.warning("%s extract failed: %s", provider, exc)
                self.failures.append(f"{provider}: {exc}")
                continue
            if not data:
                self.failures.append(f"{provider}: empty reply")
                continue
            self.last_provider = provider
            return data
        return None


def _candidate_detail_block(forensics: list[PerCaseForensics]) -> list[tuple[str, str, str]]:
    """(id, title, first-sentence description) for likely-relevant techniques."""
    seen: set[str] = set()
    detail: list[tuple[str, str, str]] = []
    for record in forensics:
        for tid in record.candidate_technique_ids:
            tid = tid.upper()
            if tid in seen:
                continue
            seen.add(tid)
            meta = _technique_meta(tid)
            if meta is None:
                continue
            detail.append((tid, meta[0], meta[1]))
            if len(detail) >= 20:
                return detail
    return detail


def extract_ttps_for_links(
    index: ArticleSearchIndex,
    links: list[str],
    *,
    settings: Settings | None = None,
) -> ExtractTtpsResponse:
    cfg = settings or get_settings()
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

    stage1_candidates = resolve_extract_providers(cfg, stage="stage1")
    stage2_candidates = resolve_extract_providers(cfg, stage="stage2")
    if not stage1_candidates and not stage2_candidates:
        if (cfg.extract_llm_provider or "auto").strip().lower() != "none":
            floor.detail += " · LLM off (no XAI/ANTHROPIC/GEMINI/OPENAI key)"
        return floor

    # Stage 1: deep-read the richest articles; everything else gets a
    # floor-derived forensic record so synthesis always sees the whole board.
    deep_set = (
        select_deep_articles(articles, max_articles=cfg.extract_deep_max_articles)
        if stage1_candidates
        else []
    )
    stage1_results: dict[str, PerCaseForensics] = {}
    cached = 0
    stage1_failures: list[str] = []
    if deep_set:
        enrich: dict[str, str] = {}
        with httpx.Client(timeout=20.0) as client:
            for article in deep_set:
                extra = enrich_courtlistener_snippet(
                    article, token=cfg.courtlistener_api_token, client=client
                )
                if extra:
                    enrich[article.link] = extra
        stage1_caller = _ProviderCaller(stage1_candidates, cfg)
        stage1_results, cached, stage1_failures = deep_extract.run_stage1(
            deep_set, cfg=cfg, call_llm=stage1_caller, enrich=enrich
        )
    forensics = [stage1_results.get(a.link) or forensics_from_floor(a) for a in articles]
    deep_count = sum(1 for r in forensics if r.extraction_status == "llm")
    floor_count = len(forensics) - deep_count

    # Stage 2: one synthesis call over every forensic record.
    synthesis: dict[str, Any] | None = None
    stage2_caller = _ProviderCaller(stage2_candidates, cfg)
    if stage2_candidates:
        user = build_stage2_user_prompt(forensics, _candidate_detail_block(forensics))
        synthesis = stage2_caller(STAGE2_SYSTEM_PROMPT, user, STAGE2_MAX_TOKENS)
        logger.info(
            "extract deep: %d stage1 (%d cached, %d failed) + 1 synthesis via %s",
            len(deep_set),
            cached,
            len(stage1_failures),
            stage2_caller.last_provider or "none",
        )

    if synthesis:
        report = _merge_synthesis(floor, synthesis, articles=articles)
        report.mode = "llm"
        report.report_version = 2
        report.detail = (
            f"LLM deep ({stage2_caller.last_provider}) · "
            f"{deep_count} deep / {floor_count} floor source(s)"
        )
        if stage1_failures:
            report.detail += f" · {len(stage1_failures)} deep extraction(s) failed"
    elif deep_count:
        # Synthesis failed but per-article extraction worked — group mechanically.
        report = _mechanical_sections(floor, forensics)
        report.mode = "llm"
        report.report_version = 2
        report.detail = (
            f"Synthesis failed — per-case extraction only · "
            f"{deep_count} deep / {floor_count} floor source(s)"
        )
    else:
        failures = stage1_failures + stage2_caller.failures
        floor.detail += f" · LLM failed ({'; '.join(failures)[:400]})"
        return floor

    for section in report.techniques:
        _attach_controls(section)
    _derive_legacy_fields(report, forensics)
    return report
