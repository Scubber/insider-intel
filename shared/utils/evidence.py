"""Insider Evidence Ledger aggregation core.

Rolls stored forensic records (plain dicts — works on raw JSONL rows or
``ProcessedArticle.model_dump()`` output) into the ledger consumed by the
``evidence-ledger`` workflow's markdown report and the API's
``GET /evidence/ledger`` sidebar payload. Pure stdlib so the bare Actions
runner can import it; no LLM spend anywhere in this path.

Counting rules (keep these honest):
- Verdict gate (D-contamination, 2026-08-16 audit): only rows the enricher
  adjudicated ``is_insider_case is True`` contribute to insider-behavior
  aggregates. Non-insider and unadjudicated (missing/None verdict) rows are
  counted in the ``basis`` block but contribute NOTHING — before the gate,
  ~33.7% of contributing rows were non-insider.
- A case's strength is the STRONGEST claim_status any of its methods carries,
  CAPPED by the document's ``legal_posture`` (D-posture): ``adjudicated``/
  ``admitted`` methods are ground truth; ``alleged`` is a complaint's theory —
  the two are never conflated, and a civil complaint can never mint an
  adjudicated case no matter how its text reads (see POSTURE_WEIGHT).
- ``mechanically_implied`` observables are the defensible detection claims.
- Every ledger is stamped (D-staleness): ``generated_at`` plus a ``basis``
  block (row counts through the gate, model mix) so renderers can say
  "based on N cases as of DATE" instead of implying freshness.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime

STRONG_STATUSES = {"adjudicated", "admitted"}
STRENGTH_RANK = {"reported/unclear": 0, "alleged": 1, "adjudicated/admitted": 2}

# D-posture (2026-08-16 audit): ordinal weight of each legal stage — how much
# finding-of-fact force the DOCUMENT itself carries. claim_status is the LLM's
# read of how one document frames an action; inside a civil complaint,
# "adjudicated"-sounding language is still the plaintiff's theory. Posture
# therefore CAPS a case's strength, never raises it: a posture below
# POSTURE_ADJUDICATED_MIN_WEIGHT can never count as adjudicated/admitted.
# Ordering: conviction/sentencing (court findings) ≥ plea (admission on the
# record) > settlement (resolved, but typically no admission or finding) >
# indictment (a grand jury's charge — probable cause, not proof) >
# complaint/civil_suit (one side's allegations). "none"/"unknown" carry no
# document signal, so claim_status stands uncapped rather than degrading
# legacy rows on absent data.
POSTURE_WEIGHT: dict[str, int] = {
    "conviction": 5,
    "sentencing": 5,
    "plea": 4,
    "settlement": 3,
    "indictment": 2,
    "civil_suit": 1,
    "complaint": 1,
    # Indian procedural stages (2026-08). Indian judgments are often
    # forensically rich at PRE-adjudication stages — a bail order can recite
    # exact email accounts and dates — so every pre-adjudicative stage sits
    # below POSTURE_ADJUDICATED_MIN_WEIGHT and its "adjudicated" claims are
    # capped to alleged-tier. Bail/quashing/interim relief are procedural, not
    # findings on the conduct; a disciplinary or writ record is an employment
    # process, never a criminal adjudication. Deliberate calls: acquittal is
    # adjudicated AGAINST proof of the conduct, so it caps like a charge-stage
    # document rather than letting "adjudicated" methods count as proven;
    # trial_judgment/civil_decree ARE merits findings and rank adjudicated.
    "trial_judgment": 5,
    "civil_decree": 4,
    "arbitral_proceeding": 3,
    "charge_sheet": 2,
    "disciplinary_proceeding": 2,
    "interim_injunction": 2,
    "writ_review": 2,
    "acquittal": 2,
    "fir_allegation": 1,
    "bail": 1,
    "quashing": 1,
}
POSTURE_ADJUDICATED_MIN_WEIGHT = 4
CHANNELS = ("email", "chat", "network", "endpoint", "cloud", "identity", "physical", "human")

# Authored source→jurisdiction taxonomy (the country of the COURT SYSTEM the
# record came from — never the actor's nationality). Explicit legal metadata
# always wins over this prefix fallback. Kept here (pure stdlib) so both the
# API's ledger path and the bare-Actions-runner path resolve identically.
SOURCE_COUNTRY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("courtlistener", "US"),
    ("pacer", "US"),
    ("canlii-", "CA"),
    ("indiacourts-", "IN"),
)


def resolve_country(source_id, legal_metadata=None) -> str | None:
    """Jurisdiction code for a corpus row: explicit metadata, then prefixes.

    Returns None for rows with no court provenance (news, social, …) — those
    are not "US by default"; only court-document lanes carry a jurisdiction.
    """
    if isinstance(legal_metadata, dict):
        code = str(legal_metadata.get("country_code") or "").strip().upper()
        if code:
            return code
    sid = str(source_id or "").strip().lower()
    for prefix, country in SOURCE_COUNTRY_PREFIXES:
        if sid.startswith(prefix):
            return country
    return None


def resolve_article_country(article) -> str | None:
    """`resolve_country` for an article-shaped object (duck-typed, no imports).

    Single home for the jurisdiction rule so the search index and the NDJSON
    export can never silently disagree on a row's country.
    """
    legal = getattr(article, "legal_metadata", None)
    if legal is not None and not isinstance(legal, dict):
        legal = legal.model_dump() if hasattr(legal, "model_dump") else None
    return resolve_country(getattr(article, "source_id", None), legal)


def filter_rows_by_country(rows, country: str):
    """Rows whose resolved jurisdiction matches ``country`` (case-insensitive).

    Used by the API's ``?country=`` ledger slicing and by the Actions runner;
    the ledger builder itself stays slice-agnostic (one engine, many views).
    """
    want = (country or "").strip().upper()
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        got = resolve_country(row.get("source_id"), row.get("legal_metadata"))
        if got == want:
            out.append(row)
    return out

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

# Caps on stored per-technique hunt material (deduped by normalized text).
HUNTS_PER_TECHNIQUE = 12
TERMS_PER_TECHNIQUE = 20
BEHAVIORS_PER_TECHNIQUE = 15

_ENTITY_SUFFIXES = (
    " llc",
    " inc",
    " ltd",
    " corp",
    " corporation",
    " company",
    " group",
    " holdings",
    " partners",
    " studios",
    " logistics",
    " resources",
)


def is_entity_term(term: str) -> bool:
    """True for case-specific proper nouns (people, companies, domains, dates).

    Hunt terms extracted from a case mix generic behavior indicators
    ("personal email account") with entities specific to that case
    ("Holly Hill Logistics", "Robert Dawson", "@holcim.com") — the latter
    will never appear in another environment and only pollute a hunt.
    """
    t = term.strip()
    if not t:
        return True
    tl = t.lower()
    if "@" in t:
        return True
    if any(tl.endswith(s) or f"{s} " in f" {tl} " for s in _ENTITY_SUFFIXES):
        return True
    words = t.split()
    if len(words) >= 2:
        alpha = [w for w in words if w[:1].isalpha()]
        capped = [w for w in alpha if w[:1].isupper()]
        # Mostly-capitalized multi-word phrases are names, not indicators.
        if alpha and len(capped) / len(alpha) >= 0.6:
            return True
    return False


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


def case_strength(methods: list[dict], legal_posture: str = "") -> str:
    """Strongest claim_status across a case's methods, capped by legal posture.

    D-posture (2026-08-16 audit): without the cap, allegations in complaints
    were weighted like adjudicated facts whenever the enricher stamped a
    method "adjudicated" while reading a complaint. The document's legal
    stage now sets a ceiling (see POSTURE_WEIGHT): postures below
    POSTURE_ADJUDICATED_MIN_WEIGHT cap the case at "alleged". Posture never
    PROMOTES — an alleged-only method list stays alleged even under a
    conviction posture — and an unknown/absent posture leaves claim_status
    uncapped so legacy rows don't degrade on missing data.
    """
    statuses = {str(m.get("claim_status") or "unclear").lower() for m in methods}
    if statuses & STRONG_STATUSES:
        strength = "adjudicated/admitted"
    elif "alleged" in statuses:
        strength = "alleged"
    else:
        strength = "reported/unclear"
    weight = POSTURE_WEIGHT.get(str(legal_posture or "").strip().lower())
    if (
        weight is not None
        and weight < POSTURE_ADJUDICATED_MIN_WEIGHT
        and STRENGTH_RANK[strength] > STRENGTH_RANK["alleged"]
    ):
        return "alleged"
    return strength


def build_evidence_ledger(rows, *, top: int = 25, now: datetime | None = None) -> dict:
    """Aggregate corpus rows (dicts) into the ledger payload.

    Each row needs ``link``/``title``/``published`` and a ``forensics`` dict
    with ``methods`` (and optionally ``candidate_technique_ids``). Rows
    without extracted methods are counted but contribute nothing.

    Verdict gate (D-contamination): a method-bearing row contributes to the
    insider aggregates only when ``forensics.is_insider_case is True`` — same
    semantics as the projection layer's verdict-gated selection
    (``shared.schemas.forensics.select_best_enrichment``). Rows adjudicated
    non-insider (False) or never adjudicated (missing/None) are tallied in
    ``basis`` and skipped. ``enriched_cases`` therefore now means
    "verdict-true cases with extracted methods" (name kept for the site).

    ``now`` (D-staleness) injects the ``generated_at`` stamp for
    deterministic tests; defaults to the current UTC time.
    """
    total_rows = enriched_cases = 0
    enriched_rows = verdict_true_rows = 0
    excluded_non_insider = excluded_no_verdict = 0
    posture_mix: Counter = Counter()
    posture_capped = 0
    model_mix: Counter = Counter()
    quotes_verbatim = quotes_paraphrased = quotes_unstamped = 0
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
    tech_terms: dict[str, list[str]] = defaultdict(list)  # technique -> case hunt terms
    term_seen: dict[str, set] = defaultdict(set)
    tech_behaviors: dict[str, list[dict]] = defaultdict(list)  # technique -> observed actions
    behavior_seen: dict[str, set] = defaultdict(set)
    year_tech: dict[str, Counter] = defaultdict(Counter)
    strength_totals: Counter = Counter()
    country_mix: Counter = Counter()  # jurisdiction of contributing cases

    for row in rows:
        if not isinstance(row, dict):
            continue
        total_rows += 1
        f = row.get("forensics")
        f = f if isinstance(f, dict) else {}
        if f:
            enriched_rows += 1
        verdict = f.get("is_insider_case")
        if f and verdict is True:
            verdict_true_rows += 1
        methods = [m for m in (f.get("methods") or []) if isinstance(m, dict)]
        if not methods:
            continue
        # D-contamination gate: insider-behavior statistics only from rows the
        # enricher adjudicated as insider cases. False and missing/None both
        # fail the gate (uncertainty is not evidence).
        if verdict is not True:
            if verdict is False:
                excluded_non_insider += 1
            else:
                excluded_no_verdict += 1
            continue
        enriched_cases += 1
        model_mix[str(f.get("model") or "").strip() or "unknown"] += 1
        row_country = resolve_country(row.get("source_id"), row.get("legal_metadata"))
        if row_country:
            country_mix[row_country] += 1
        link = str(row.get("link") or f"row-{total_rows}")
        title = str(row.get("title") or link)[:90]
        posture = str(f.get("legal_posture") or "unknown").strip().lower() or "unknown"
        posture_mix[posture] += 1
        strength = case_strength(methods, posture)
        if strength != case_strength(methods):
            posture_capped += 1
        strength_totals[strength] += 1
        year = str(row.get("published") or "")[:4] or "????"

        # Quote grounding: tally the deterministic evidence_quote_verbatim
        # stamps (shared.schemas.forensics.stamp_quote_verbatim) so the page
        # can state what share of surfaced quotes is verified verbatim.
        for m in methods:
            if not str(m.get("evidence_quote") or "").strip():
                continue
            stamp = m.get("evidence_quote_verbatim")
            if stamp is True:
                quotes_verbatim += 1
            elif stamp is False:
                quotes_paraphrased += 1
            else:
                quotes_unstamped += 1

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
        # Keep only behavior indicators — case-specific entities (defendant
        # names, vendor companies) won't appear in any other environment.
        case_terms = [
            t
            for t in (str(x).strip() for x in (f.get("hunt_terms") or []))
            if t and not is_entity_term(t)
        ]
        # (action, verbatim-verified quote) pairs — only quotes stamped
        # evidence_quote_verbatim=True ride along; paraphrases/fabrications
        # (False) and unstamped legacy quotes surface as no quote at all.
        case_actions = [
            (
                action,
                (
                    str(m.get("evidence_quote") or "").strip()
                    if m.get("evidence_quote_verbatim") is True
                    else ""
                ),
            )
            for m, action in ((m, str(m.get("action") or "").strip()) for m in methods)
            if action
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
            for term in case_terms:
                key = " ".join(term.lower().split())
                if key in term_seen[tech] or len(tech_terms[tech]) >= TERMS_PER_TECHNIQUE:
                    continue
                term_seen[tech].add(key)
                tech_terms[tech].append(term[:80])
            for action, quote in case_actions:
                key = " ".join(action.lower().split())[:120]
                full = len(tech_behaviors[tech]) >= BEHAVIORS_PER_TECHNIQUE
                if key in behavior_seen[tech] or full:
                    continue
                behavior_seen[tech].add(key)
                tech_behaviors[tech].append(
                    {
                        "action": action[:160],
                        "strength": strength,
                        # Verbatim-True source quote or None — never a paraphrase.
                        "quote": quote[:200] or None,
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

    quoted_methods = quotes_verbatim + quotes_paraphrased + quotes_unstamped
    return {
        "total_rows": total_rows,
        "enriched_cases": enriched_cases,
        "small_n_floor": SMALL_N_FLOOR,
        # D-staleness: generation stamp + the row counts behind every number,
        # so the site can render a "based on N cases as of DATE" basis banner.
        "generated_at": (now or datetime.now(UTC)).isoformat(),
        "basis": {
            "corpus_rows": total_rows,
            "enriched_rows": enriched_rows,
            "verdict_true_rows": verdict_true_rows,
            "contributing_cases": enriched_cases,
            "excluded_non_insider": excluded_non_insider,
            "excluded_no_verdict": excluded_no_verdict,
            "model_mix": dict(sorted(model_mix.items(), key=lambda kv: (-kv[1], kv[0]))),
        },
        # Jurisdictions with contributing cases (court system of the source
        # records, never actor nationality). Drives the EVIDENCE nation tabs;
        # slicing happens via filter_rows_by_country BEFORE this builder, so
        # one engine renders the global and every per-country view.
        "countries": dict(sorted(country_mix.items(), key=lambda kv: (-kv[1], kv[0]))),
        # D-posture: document-stage mix of contributing cases plus how many
        # had their method-claimed strength demoted by the posture ceiling.
        "posture": {
            "mix": dict(sorted(posture_mix.items(), key=lambda kv: (-kv[1], kv[0]))),
            "capped_cases": posture_capped,
        },
        # Quote grounding: share of contributing cases' claimed quotes whose
        # deterministic verbatim stamp verified them against the source.
        "quote_grounding": {
            "quoted_methods": quoted_methods,
            "verbatim_true": quotes_verbatim,
            "verbatim_false": quotes_paraphrased,
            "unstamped": quotes_unstamped,
            "verbatim_share_pct": (
                round(100 * quotes_verbatim / quoted_methods) if quoted_methods else None
            ),
        },
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
        "technique_terms": {t: terms for t, terms in tech_terms.items()},
        # Adjudicated first; within a strength, verbatim-quoted behaviors
        # outrank quote-less ones (quote-grounding preference).
        "technique_behaviors": {
            t: sorted(
                b,
                key=lambda x: (x["strength"] != "adjudicated/admitted", x["quote"] is None),
            )
            for t, b in tech_behaviors.items()
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
