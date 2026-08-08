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
    (
        r"vpn|network log|server (request|log)|application log",
        "server & application logs (VPN, web, app)",
    ),
    (r"badge|physical", "badge / physical access records"),
    (
        r"earnings call|public (statement|disclosure|filing)|press release|proxy",
        "public statements vs internal records",
    ),
    (
        r"corporate (registration|formation)|account.?opening|onboarding",
        "entity-formation / account-opening records",
    ),
    (
        r"endpoint|edr|device|forensic imag",
        "workstation/device artifacts (EDR, disk forensics)",
    ),
    (r"training|policy|manual|guideline", "internal policy / training materials"),
    (r"siem|audit log", "central audit trails (SIEM, audit logs)"),
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


# ITM theme by technique-id prefix (the matrix's own spine).
THEME_BY_PREFIX = {
    "MT": "motive",
    "ME": "means",
    "PR": "preparation",
    "IF": "infringement",
    "AF": "anti-forensics",
}
THEMES = ("motive", "means", "preparation", "infringement", "anti-forensics")

# Small-n honesty: percentages built on fewer cases than this are suppressed
# (renderers show counts only). One misleading "75% of temps" (n=4) costs the
# report its credibility.
SMALL_N_FLOOR = 10

# Cap on stored hunt seeds per technique (deduped by normalized logic).
HUNTS_PER_TECHNIQUE = 12

# WHO — roles, never individuals. Two independent axes normalized from the
# free-text actor_role/actor_profile the enricher extracts per case.
_ROLE_FUNCTIONS: list[tuple[str, str]] = [
    (
        r"ceo|cfo|coo|cto|ciso|chief|president|founder|officer|director|board"
        r"|executive|vp\b|vice.?president",
        "executive/officer",
    ),
    (r"manager|supervisor|head of|lead\b", "manager"),
    (
        r"engineer|developer|admin(istrator)?|analyst|scientist|technician"
        r"|architect|programmer|it |sysadmin|dba\b",
        "technical",
    ),
    (
        r"sales|broker|trader|account (exec|manager|representative)"
        r"|advisor|adviser|banker|finance|accountant",
        "sales/finance",
    ),
    (
        r"contractor|consultant|vendor|third.?party|supplier|outsourc|freelanc|subcontract",
        "contractor/vendor",
    ),
    (r"temp\b|temporary|intern\b|seasonal|part.?time", "temp/intern"),
]
_ROLE_STATES: list[tuple[str, str]] = [
    # Order matters: "former"/"fired" outranks "departing" outranks default.
    (
        r"former|fired|terminated|ex-employee|ex employee|dismissed|laid off"
        r"|after (his |her |their )?(termination|departure|resignation)",
        "former/fired",
    ),
    (
        r"departing|resign|on notice|before (leaving|departure|joining a competitor)"
        r"|outgoing|leaving",
        "departing",
    ),
    (r"contractor|consultant|vendor|third.?party|supplier|partner", "third-party"),
]
_ROLE_FN_RX = [(re.compile(p, re.I), label) for p, label in _ROLE_FUNCTIONS]
_ROLE_ST_RX = [(re.compile(p, re.I), label) for p, label in _ROLE_STATES]


def normalize_role(*texts: str) -> tuple[str, str]:
    """(function, employment_state) from free-text role strings.

    Unmatched → ("unknown", ...); a matched function with no state signal
    defaults to "current" (filings name past-tense roles, so state only moves
    off current on explicit boundary language).
    """
    blob = " ".join(t for t in texts if t).strip()
    if not blob:
        return ("unknown", "unknown")
    function = "unknown"
    for rx, label in _ROLE_FN_RX:
        if rx.search(blob):
            function = label
            break
    state = "unknown"
    for rx, label in _ROLE_ST_RX:
        if rx.search(blob):
            state = label
            break
    if state == "unknown":
        state = "current" if function != "unknown" else "unknown"
    return (function, state)


# Corroboration crosswalk: evidence record-class families ↔ ITM Detection ids.
# Hand-authored and conservative — a DT is corroborated only when a case's
# observed record class is squarely the artifact that detection inspects.
# External/legal record classes (Form 4s, brokerage records, public statements)
# deliberately map to nothing except Financial Auditing: courts often convict
# on records ORG TELEMETRY NEVER SEES — itself a finding the page states.
EVIDENCE_DT_CROSSWALK: dict[str, tuple[str, ...]] = {
    "removable-media (USB) logs": (
        "DT020",
        "DT021",
        "DT022",
        "DT023",
        "DT024",
        "DT025",
        "DT087",
        "DT149",
    ),
    "email logs / content": ("DT040", "DT041", "DT140", "DT141"),
    "system/file access logs": ("DT037", "DT052", "DT094", "DT110", "DT146"),
    "workstation/device artifacts (EDR, disk forensics)": (
        "DT026",
        "DT027",
        "DT036",
        "DT038",
        "DT043",
        "DT045",
        "DT046",
    ),
    "central audit trails (SIEM, audit logs)": (
        "DT052",
        "DT062",
        "DT063",
        "DT064",
        "DT065",
        "DT066",
        "DT094",
    ),
    "server & application logs (VPN, web, app)": (
        "DT039",
        "DT042",
        "DT051",
        "DT096",
        "DT097",
        "DT098",
        "DT100",
    ),
    "personal cloud storage contents": ("DT048", "DT135", "DT142"),
    "print logs": ("DT005", "DT006", "DT007", "DT139"),
    "badge / physical access records": ("DT033", "DT103", "DT137"),
    "personal messaging / phone records": ("DT107", "DT154", "DT155"),
    "authentication logs": ("DT050", "DT062", "DT063", "DT068"),
    "brokerage / trade records": ("DT152",),  # Financial Auditing (DT067 pre-ITM-2.9)
    "SEC Form 4 / insider-transaction filings": ("DT152",),
}


def corroborate_detections(detections: list[dict], observed_families: dict[str, int]) -> list[dict]:
    """Stamp each ITM detection ref with real-case corroboration.

    ``detections`` — [{"id","title"}, ...] from the catalog for one technique.
    ``observed_families`` — {record-class family: case count} seen in cases
    exhibiting that technique. A detection is corroborated when any observed
    family crosswalks to it; ``cases`` sums the supporting families' counts.
    """
    family_by_dt: dict[str, list[str]] = {}
    for fam, dts in EVIDENCE_DT_CROSSWALK.items():
        for dt in dts:
            family_by_dt.setdefault(dt, []).append(fam)
    out = []
    for det in detections:
        fams = [f for f in family_by_dt.get(str(det.get("id", "")), []) if f in observed_families]
        out.append(
            {
                "id": det.get("id"),
                "title": det.get("title"),
                "corroborated": bool(fams),
                "cases": sum(observed_families[f] for f in fams),
                "via": fams,
            }
        )
    return out


def technique_theme(tech_id: str) -> str:
    return THEME_BY_PREFIX.get(str(tech_id)[:2].upper(), "other")


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
    tech_families: dict[str, Counter] = defaultdict(Counter)  # technique -> family -> cases
    role_fn: dict[str, Counter] = defaultdict(Counter)  # function -> strength -> cases
    role_state: dict[str, Counter] = defaultdict(Counter)
    role_known = 0
    artifact_cases: dict[str, set] = defaultdict(set)
    artifact_strong: dict[str, set] = defaultdict(set)
    artifact_mech: Counter = Counter()
    artifact_inferred: Counter = Counter()
    artifact_examples: dict[str, list[str]] = defaultdict(list)
    artifact_channels: dict[str, Counter] = defaultdict(Counter)
    channel_cases: dict[str, set] = defaultdict(set)
    tech_hunts: dict[str, list[dict]] = defaultdict(list)  # technique -> hunt seeds
    hunt_seen: dict[str, set] = defaultdict(set)
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

        function, emp_state = normalize_role(
            str(f.get("actor_role") or ""), str(f.get("actor_profile") or "")
        )
        if function != "unknown" or emp_state != "unknown":
            role_known += 1
        role_fn[function][strength] += 1
        role_state[emp_state][strength] += 1

        case_families: set[str] = set()
        for m in methods:
            for obs in m.get("observables") or []:
                if isinstance(obs, dict):
                    case_families.add(artifact_family(str(obs.get("artifact") or "")))

        # Case-grounded hunt seeds from the enricher — actual query logic
        # derived from what this insider did, not catalog keywords.
        case_hunts = [
            hq
            for hq in (f.get("hunt_queries") or [])
            if isinstance(hq, dict) and str(hq.get("logic") or "").strip()
        ]

        for tech in f.get("candidate_technique_ids") or []:
            tech = str(tech).upper().strip()
            if not tech:
                continue
            tech_cases[tech][strength] += 1
            if len(tech_examples[tech]) < 3:
                tech_examples[tech].append(title)
            year_tech[year][tech] += 1
            for fam in case_families:
                tech_families[tech][fam] += 1
            for hq in case_hunts:
                logic = str(hq.get("logic")).strip()
                key = " ".join(logic.lower().split())
                if key in hunt_seen[tech] or len(tech_hunts[tech]) >= HUNTS_PER_TECHNIQUE:
                    continue
                hunt_seen[tech].add(key)
                tech_hunts[tech].append(
                    {
                        "stack": str(hq.get("stack") or "SIEM").strip()[:40] or "SIEM",
                        "logic": logic[:300],
                        "rationale": str(hq.get("rationale") or "").strip()[:200],
                        "case": title,
                        "strength": strength,
                    }
                )

        for m in methods:
            m_strong = str(m.get("claim_status") or "").lower() in STRONG_STATUSES
            for obs in m.get("observables") or []:
                if not isinstance(obs, dict):
                    continue
                raw_artifact = str(obs.get("artifact") or "").strip()
                fam = artifact_family(raw_artifact)
                artifact_cases[fam].add(link)
                if m_strong:
                    artifact_strong[fam].add(link)
                if str(obs.get("basis") or "") == "mechanically_implied":
                    artifact_mech[fam] += 1
                else:
                    artifact_inferred[fam] += 1
                if (
                    raw_artifact
                    and raw_artifact.lower() not in {e.lower() for e in artifact_examples[fam]}
                    and len(artifact_examples[fam]) < 3
                ):
                    artifact_examples[fam].append(raw_artifact[:70])
                ch = str(obs.get("channel") or "").lower()
                if ch in CHANNELS:
                    artifact_channels[fam][ch] += 1
                    channel_cases[ch].add(link)

    strong_total = strength_totals["adjudicated/admitted"]
    ranked_techs = sorted(tech_cases.items(), key=lambda kv: -sum(kv[1].values()))
    ranked_artifacts = sorted(artifact_cases.items(), key=lambda kv: -len(kv[1]))

    theme_rollup: dict[str, dict] = {
        t: {"cases": 0, "adjudicated_admitted": 0, "techniques": 0} for t in THEMES
    }
    for tech, counts in tech_cases.items():
        theme = technique_theme(tech)
        if theme in theme_rollup:
            theme_rollup[theme]["cases"] += sum(counts.values())
            theme_rollup[theme]["adjudicated_admitted"] += counts["adjudicated/admitted"]
            theme_rollup[theme]["techniques"] += 1

    def _axis(counter_map: dict[str, Counter]) -> list[dict]:
        rows = []
        for label, counts in counter_map.items():
            total = sum(counts.values())
            rows.append(
                {
                    "label": label,
                    "cases": total,
                    "adjudicated_admitted": counts["adjudicated/admitted"],
                    # Small-n honesty: no share when the base is too thin.
                    "share_pct": (
                        round(100 * total / enriched_cases)
                        if enriched_cases and total >= SMALL_N_FLOOR
                        else None
                    ),
                }
            )
        rows.sort(key=lambda r: (r["label"] == "unknown", -r["cases"]))
        return rows

    return {
        "total_rows": total_rows,
        "enriched_cases": enriched_cases,
        "small_n_floor": SMALL_N_FLOOR,
        "strength_totals": {
            "adjudicated_admitted": strong_total,
            "alleged": strength_totals["alleged"],
            "reported_unclear": strength_totals["reported/unclear"],
        },
        "roles": {
            "known": role_known,
            "function": _axis(role_fn),
            "employment_state": _axis(role_state),
        },
        "themes": [
            {"theme": t, **theme_rollup[t]} for t in THEMES if theme_rollup[t]["techniques"]
        ],
        "techniques": [
            {
                "id": tech,
                "theme": technique_theme(tech),
                "cases": sum(counts.values()),
                "adjudicated_admitted": counts["adjudicated/admitted"],
                "alleged": counts["alleged"],
                "exemplars": tech_examples[tech],
                "top_families": [
                    {"artifact": fam, "cases": n} for fam, n in tech_families[tech].most_common(5)
                ],
            }
            for tech, counts in ranked_techs[:top]
        ],
        # Full per-technique family map (observed techniques only) so the API
        # can serve arbitrary technique detail without re-aggregating.
        "technique_families": {t: dict(c) for t, c in tech_families.items()},
        "technique_counts": {
            t: {
                "cases": sum(c.values()),
                "adjudicated_admitted": c["adjudicated/admitted"],
                "alleged": c["alleged"],
            }
            for t, c in tech_cases.items()
        },
        # Per-technique case-derived hunt seeds (deduped by logic, adjudicated
        # cases first) so the dossier can show how real cases were huntable.
        "technique_hunts": {
            t: sorted(hunts, key=lambda h: h["strength"] != "adjudicated/admitted")
            for t, hunts in tech_hunts.items()
        },
        "detected_by": [
            {
                "artifact": fam,
                "cases": len(links),
                "adjudicated_admitted_cases": len(artifact_strong[fam]),
                "adjudicated_share": (
                    round(100 * len(artifact_strong[fam]) / strong_total) if strong_total else None
                ),
                "mechanical_observables": artifact_mech[fam],
                "inferred_observables": artifact_inferred[fam],
                "examples": artifact_examples[fam],
                "top_channels": [c for c, _ in artifact_channels[fam].most_common(3)],
            }
            for fam, links in ranked_artifacts[:top]
        ],
        "channels": {ch: len(channel_cases[ch]) for ch in CHANNELS},
        "by_year": {y: dict(c.most_common(5)) for y, c in sorted(year_tech.items())},
    }
