"""Extraction-board TTP hunt report: seed floor + optional xAI fill."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from apps.search.index import ArticleSearchIndex
from shared.schemas import ProcessedArticle
from shared.schemas.articles import resolve_channel
from shared.settings import Settings, get_settings

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 3500
# Court filings carry full RECAP/opinion bodies worth reading in depth.
FILINGS_TEXT_MAX_CHARS = 12_000
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


class ExtractTtpsResponse(BaseModel):
    mode: Literal["llm", "seeds"]
    article_count: int
    titles: list[str] = Field(default_factory=list)
    behaviors: list[TtpBehavior] = Field(default_factory=list)
    email: list[str] = Field(default_factory=list)
    chat: list[str] = Field(default_factory=list)
    network: list[str] = Field(default_factory=list)
    human: list[str] = Field(default_factory=list)
    seeds: list[str] = Field(default_factory=list)
    matched_if038: bool = False
    detail: str = ""


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


def _technique_behavior_text(tech_id: str, fallback_title: str) -> str:
    """First sentence of the catalog description — a behavior, not a label."""
    from shared.itm.index import load_itm_index

    for tech in load_itm_index().techniques:
        if tech.id.upper() == tech_id.upper():
            desc = (tech.description_text or "").strip()
            if desc:
                first = desc.split(". ", 1)[0].strip()
                return (first if first.endswith(".") else first + ".")[:220]
            return tech.title
    return fallback_title


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

    seen_tech: set[str] = set()
    case_methods: list[str] = []
    for article in articles:
        seeds.extend(article.entities.operator_terms or [])
        for hit in article.entities.itm_hits or []:
            seeds.extend(hit.matched_aliases or [])
            tid = str(hit.id).upper()
            if tid not in seen_tech:
                seen_tech.add(tid)
                behaviors.append(
                    TtpBehavior(id=hit.id, text=_technique_behavior_text(hit.id, hit.title))
                )
        record = getattr(article, "case_record", None)
        if record is not None:
            case_methods.extend(record.methods)
            seeds.extend(record.methods)
            seeds.extend(record.exfil_channels)
            network.extend(record.exfil_channels)
            if record.detection_trigger:
                human.append(record.detection_trigger)

    for n, method in enumerate(_uniq(case_methods), start=1):
        behaviors.append(TtpBehavior(id=f"CASE-{n:02d}", text=f"Case-observed method: {method}"))

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

    return ExtractTtpsResponse(
        mode="seeds",
        article_count=len(articles),
        titles=[a.title for a in articles],
        behaviors=behaviors,
        email=_uniq(email),
        chat=_uniq(chat),
        network=_uniq(network),
        human=_uniq(human),
        seeds=_uniq(seeds),
        matched_if038=matched,
        detail=detail or "Seed pack (no XAI_API_KEY or LLM skipped)",
    )


def _article_text_pack(article: ProcessedArticle, extra: str = "") -> str:
    parts = [
        f"Title: {article.title}",
        f"Source: {article.source_name} ({article.source_id})",
        f"Channel: {resolve_channel(article.source_id, getattr(article, 'channel', None))}",
        f"Link: {article.link}",
    ]
    if article.summary:
        parts.append(f"Summary:\n{article.summary}")
    if article.ai_summary:
        parts.append(f"AI summary:\n{article.ai_summary}")
    record = getattr(article, "case_record", None)
    if record is not None and (record.is_insider_case or record.methods or record.exfil_channels):
        lines = ["Case record:"]
        for label, value in (
            ("actor_role", record.actor_role),
            ("access_vector", record.access_vector),
            ("timeframe", record.timeframe),
            ("detection_trigger", record.detection_trigger),
            ("outcome", record.outcome),
        ):
            if value:
                lines.append(f"- {label}: {value}")
        for label, values in (
            ("motive_signals", record.motive_signals),
            ("methods", record.methods),
            ("exfil_channels", record.exfil_channels),
        ):
            if values:
                lines.append(f"- {label}: {'; '.join(values)}")
        parts.append("\n".join(lines))
    # Raw text last so MAX_TEXT_CHARS truncation eats prose, not structure.
    if article.clean_text:
        parts.append(f"Text:\n{article.clean_text}")
    if extra:
        parts.append(f"CourtListener enrich:\n{extra}")
    blob = "\n\n".join(parts)
    channel = resolve_channel(article.source_id, getattr(article, "channel", None))
    cap = FILINGS_TEXT_MAX_CHARS if channel == "filings" else MAX_TEXT_CHARS
    if len(blob) > cap:
        return blob[: cap - 20] + "\n…[truncated]"
    return blob


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


def _merge_llm_into_floor(
    floor: ExtractTtpsResponse,
    llm: dict[str, Any],
) -> ExtractTtpsResponse:
    behaviors = list(floor.behaviors)
    for item in llm.get("behaviors") or []:
        if not isinstance(item, dict):
            continue
        bid = str(item.get("id") or "").strip() or "TTP-LLM"
        text = str(item.get("text") or item.get("behavior") or "").strip()
        if not text:
            continue
        if any(b.id == bid and b.text == text for b in behaviors):
            continue
        behaviors.append(TtpBehavior(id=bid, text=text))

    def merge_list(existing: list[str], key: str) -> list[str]:
        extra = llm.get(key) or []
        if not isinstance(extra, list):
            return existing
        return _uniq(existing + [str(x) for x in extra])

    return ExtractTtpsResponse(
        mode="llm",
        article_count=floor.article_count,
        titles=floor.titles,
        behaviors=behaviors,
        email=merge_list(floor.email, "email"),
        chat=merge_list(floor.chat, "chat"),
        network=merge_list(floor.network, "network"),
        human=merge_list(floor.human, "human"),
        seeds=merge_list(floor.seeds, "seeds"),
        matched_if038=floor.matched_if038,
        detail=f"LLM · {floor.article_count} source(s)",
    )


def _call_xai(
    packs: list[str],
    *,
    api_key: str,
    model: str,
) -> dict[str, Any] | None:
    system = (
        "You are an insider-risk investigator assistant. Given OSINT article/filing "
        "text packs, extract multi-channel hunt cues for email, chat, network, and "
        "human/HR investigation — not SIEM-only. Prefer IF038 / overemployment / "
        "undisclosed concurrent employment cues when relevant. Respond with JSON only:\n"
        "{"
        '"behaviors":[{"id":"TTP-…","text":"…"}],'
        '"email":["…"],"chat":["…"],"network":["…"],"human":["…"],"seeds":["…"]'
        "}\n"
        "Ground cues in the provided texts. Do not invent case facts not supported by text."
    )
    user = "Analyze these board articles:\n\n" + "\n\n---\n\n".join(packs)
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(XAI_CHAT_URL, headers=headers, json=body)
        if resp.status_code >= 400:
            logger.warning("xAI extract HTTP %s: %s", resp.status_code, resp.text[:300])
            return None
        data = resp.json()
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        if not content.strip():
            return None
        # Strip optional markdown fences
        cleaned = content.strip()
        fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", cleaned)
        if fence:
            cleaned = fence.group(1).strip()
        return json.loads(cleaned)


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

    packs: list[str] = []
    with httpx.Client(timeout=20.0) as client:
        for article in articles:
            extra = enrich_courtlistener_snippet(
                article,
                token=cfg.courtlistener_api_token,
                client=client,
            )
            packs.append(_article_text_pack(article, extra=extra))

    api_key = (cfg.xai_api_key or "").strip()
    if not api_key:
        return floor

    try:
        llm = _call_xai(packs, api_key=api_key, model=cfg.xai_model)
    except Exception as exc:  # noqa: BLE001
        logger.warning("xAI extract failed: %s", exc)
        floor.detail = f"Seed pack (LLM error: {exc})"
        return floor

    if not llm:
        floor.detail = "Seed pack (LLM returned empty / HTTP error)"
        return floor

    return _merge_llm_into_floor(floor, llm)
