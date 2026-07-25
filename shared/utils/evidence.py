"""Insider Evidence Ledger aggregation core.

Rolls stored forensic records (plain dicts — works on raw JSONL rows or
``ProcessedArticle.model_dump()`` output) into the ledger consumed by the
``evidence-ledger`` workflow's markdown report and the API's
``GET /evidence/ledger`` sidebar payload. Pure stdlib so the bare Actions
runner can import it; no LLM spend anywhere in this path.

Counting rules (keep these honest):
- A case's strength is the STRONGEST claim_status any of its methods carries.
  ``adjudicated``/``admitted`` methods are ground truth; ``alleged`` is a
  complaint's theory — the two are never conflated.
- ``mechanically_implied`` observables are the defensible detection claims.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

STRONG_STATUSES = {"adjudicated", "admitted"}
CHANNELS = ("email", "chat", "network", "endpoint", "cloud", "identity", "physical", "human")

# Freeform artifact strings from the enricher vary ("EDR removable-media
# events" vs "endpoint EDR removable media logs"). Normalize into coarse
# artifact families for counting; unmatched strings fall through verbatim
# (lowercased) so nothing is silently dropped.
_ARTIFACT_FAMILIES: list[tuple[str, str]] = [
    (
        r"form 4|insider (trading|transaction) (disclosure|filing)",
        "SEC Form 4 / insider-transaction filings",
    ),
    (
        r"brokerage|trade (record|execution|history|timestamp)|options (order|transaction)",
        "brokerage / trade records",
    ),
    (r"removable|usb", "removable-media (USB) logs"),
    (r"email", "email logs / content"),
    (r"text message|messaging|chat|phone record", "personal messaging / phone records"),
    (
        r"database access|data.?room access|file access|access log",
        "system/file access logs",
    ),
    (r"crm", "CRM access logs"),
    (r"cloud storage|icloud|drive", "personal cloud storage contents"),
    (r"print", "print logs"),
    (r"vpn|network log|server (request|log)|application log", "network / application logs"),
    (r"badge|physical", "badge / physical access records"),
    (
        r"earnings call|public (statement|disclosure|filing)|press release|proxy",
        "public statements vs internal records",
    ),
    (
        r"corporate (registration|formation)|account.?opening|onboarding",
        "entity-formation / account-opening records",
    ),
    (r"endpoint|edr|device|forensic imag", "endpoint forensics / EDR"),
    (r"training|policy|manual|guideline", "internal policy / training materials"),
    (r"siem|audit log", "SIEM / audit logs"),
]
_ARTIFACT_RX = [(re.compile(p, re.I), label) for p, label in _ARTIFACT_FAMILIES]


def artifact_family(artifact: str) -> str:
    text = (artifact or "").strip()
    if not text:
        return "(unspecified artifact)"
    for rx, label in _ARTIFACT_RX:
        if rx.search(text):
            return label
    return text.lower()[:80]


def case_strength(methods: list[dict]) -> str:
    """Strongest claim_status across a case's methods."""
    statuses = {str(m.get("claim_status") or "unclear").lower() for m in methods}
    if statuses & STRONG_STATUSES:
        return "adjudicated/admitted"
    if "alleged" in statuses:
        return "alleged"
    return "reported/unclear"


def build_evidence_ledger(rows, *, top: int = 25) -> dict:
    """Aggregate corpus rows (dicts) into the ledger payload.

    Each row needs ``link``/``title``/``published`` and a ``forensics`` dict
    with ``methods`` (and optionally ``candidate_technique_ids``). Rows
    without extracted methods are counted but contribute nothing.
    """
    total_rows = enriched_cases = 0
    tech_cases: dict[str, Counter] = defaultdict(Counter)
    tech_examples: dict[str, list[str]] = defaultdict(list)
    artifact_cases: dict[str, set] = defaultdict(set)
    artifact_strong: dict[str, set] = defaultdict(set)
    artifact_mech: Counter = Counter()
    artifact_channels: dict[str, Counter] = defaultdict(Counter)
    channel_cases: dict[str, set] = defaultdict(set)
    year_tech: dict[str, Counter] = defaultdict(Counter)
    strength_totals: Counter = Counter()

    for row in rows:
        if not isinstance(row, dict):
            continue
        total_rows += 1
        f = row.get("forensics") or {}
        methods = [m for m in (f.get("methods") or []) if isinstance(m, dict)]
        if not methods:
            continue
        enriched_cases += 1
        link = str(row.get("link") or f"row-{total_rows}")
        title = str(row.get("title") or link)[:90]
        strength = case_strength(methods)
        strength_totals[strength] += 1
        year = str(row.get("published") or "")[:4] or "????"

        for tech in f.get("candidate_technique_ids") or []:
            tech = str(tech).upper().strip()
            if not tech:
                continue
            tech_cases[tech][strength] += 1
            if len(tech_examples[tech]) < 3:
                tech_examples[tech].append(title)
            year_tech[year][tech] += 1

        for m in methods:
            m_strong = str(m.get("claim_status") or "").lower() in STRONG_STATUSES
            for obs in m.get("observables") or []:
                if not isinstance(obs, dict):
                    continue
                fam = artifact_family(str(obs.get("artifact") or ""))
                artifact_cases[fam].add(link)
                if m_strong:
                    artifact_strong[fam].add(link)
                if str(obs.get("basis") or "") == "mechanically_implied":
                    artifact_mech[fam] += 1
                ch = str(obs.get("channel") or "").lower()
                if ch in CHANNELS:
                    artifact_channels[fam][ch] += 1
                    channel_cases[ch].add(link)

    strong_total = strength_totals["adjudicated/admitted"]
    ranked_techs = sorted(tech_cases.items(), key=lambda kv: -sum(kv[1].values()))
    ranked_artifacts = sorted(artifact_cases.items(), key=lambda kv: -len(kv[1]))

    return {
        "total_rows": total_rows,
        "enriched_cases": enriched_cases,
        "strength_totals": {
            "adjudicated_admitted": strong_total,
            "alleged": strength_totals["alleged"],
            "reported_unclear": strength_totals["reported/unclear"],
        },
        "techniques": [
            {
                "id": tech,
                "cases": sum(counts.values()),
                "adjudicated_admitted": counts["adjudicated/admitted"],
                "alleged": counts["alleged"],
                "exemplars": tech_examples[tech],
            }
            for tech, counts in ranked_techs[:top]
        ],
        "detected_by": [
            {
                "artifact": fam,
                "cases": len(links),
                "adjudicated_admitted_cases": len(artifact_strong[fam]),
                "adjudicated_share": (
                    round(100 * len(artifact_strong[fam]) / strong_total) if strong_total else None
                ),
                "mechanical_observables": artifact_mech[fam],
                "top_channels": [c for c, _ in artifact_channels[fam].most_common(3)],
            }
            for fam, links in ranked_artifacts[:top]
        ],
        "channels": {ch: len(channel_cases[ch]) for ch in CHANNELS},
        "by_year": {y: dict(c.most_common(5)) for y, c in sorted(year_tech.items())},
    }
