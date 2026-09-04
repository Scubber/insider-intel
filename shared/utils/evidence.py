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

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
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


# Victim organization's sector (schema v3). MIRRORS ``INDUSTRIES`` in
# shared/schemas/forensics.py — this module cannot import pydantic, so the
# two are kept in step by tests/test_evidence_ledger.py's drift test.
# "unknown" is the enricher's own answer when the source is silent; rows
# from schemas that never asked (pre-v3) carry no industry key at all and
# must never be folded into it (see scripts/industry_actor_profiles.py).
INDUSTRY_LABELS: tuple[str, ...] = (
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


def resolve_industry(forensics) -> str:
    """Stored ``forensics.industry`` when it is a known label, else "unknown"."""
    if not isinstance(forensics, dict):
        return "unknown"
    value = str(forensics.get("industry") or "").strip().lower()
    return value if value in INDUSTRY_LABELS else "unknown"


def filter_rows_by_industry(rows, industry: str):
    """Rows whose stored victim-sector matches ``industry`` (case-insensitive).

    Sibling of ``filter_rows_by_country``: one engine, many views. A row with
    no forensics resolves to "unknown", so slicing on "unknown" deliberately
    includes un-enriched rows — callers wanting the v3 "unknown pool" gate on
    schema tier first (the industry script does).
    """
    want = (industry or "").strip().lower()
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if resolve_industry(row.get("forensics")) == want:
            out.append(row)
    return out


def iter_jsonl_rows(path) -> Iterator[dict]:
    """Yield the JSON objects in a JSONL file, skipping blank and corrupt lines.

    Mirrors ``JsonlProcessedStore.load_all``'s warning-skip: a torn final line
    (kill mid-append) or a non-object line is skipped, never fatal.
    """
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def collapse_rows_by_link(rows) -> list[dict]:
    """Last line wins per ``link``, returned in first-seen order.

    The processed store is append-only mid-cycle (``upsert`` appends; only
    the cycle-end ``compact`` folds), so a raw JSONL read sees every
    generation of an updated row. This is the reader-side dedupe
    ``JsonlProcessedStore.load_all`` applies — same key, same direction, same
    order — so a stdlib script and the API agree on which row is current.
    A first-wins read (the 2026-08 email scan) reports the STALE generation.
    Rows without a link are kept, keyed by their position.
    """
    by_key: dict = {}
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        link = row.get("link")
        key = link if link else ("__no_link__", idx)
        by_key[key] = row  # later rows win; dict keeps the first-seen slot
    return list(by_key.values())


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

# Record classes no COMPANY SENSOR produces. Authored deliberately, not derived:
# an earlier draft inferred this from the DT crosswalk and wrongly swept in
# account-opening records (a bank generates those daily) and "public statements
# vs internal records" (whose internal half is company-held by construction).
# These four are held by a broker, a carrier, a personal account, or a public
# registry — an employer reaches them through counsel, a regulator or consent,
# never by turning on logging.
# family -> who actually holds it, so a card can name the holder instead of
# reciting the whole list.
OUTSIDE_TELEMETRY_HOLDERS: dict[str, str] = {
    "SEC Form 4 / insider-transaction filings": "a public filing registry",
    "brokerage / trade records": "the person's broker",
    "personal messaging / phone records": "a phone carrier or a personal device",
    "personal cloud storage contents": "a personal cloud account",
}
OUTSIDE_TELEMETRY_FAMILIES = frozenset(OUTSIDE_TELEMETRY_HOLDERS)


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

# normalize_role fills employment_state with "current" whenever a function
# matched and no boundary language appeared, so that bucket is a DEFAULT and
# not a measurement. No finding may headline it — "most insiders were current
# employees" would be reporting the fill value.
DEFAULTED_EMPLOYMENT_STATE = "current"

# How many techniques the year-over-year trend surface tracks. Fixed across
# every year so an absent technique reads as zero, not as "fell out of the
# top five" (see by_year).
TREND_TECHNIQUES = 8

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
#
# ORDER IS THE CONTRACT — first match wins, so the list runs from the most
# specific relationship (outsider, temp) to the broadest title family. Every
# alternative is word-bounded. Repaired 2026-09-04: the old unbounded patterns
# sent "contractor" to executive/officer (substring `cto`), every "loan /
# compliance / trust officer" to executive (bare `officer`), "onboarding
# specialist" to executive (`board`), and "credit union employee" to technical
# (`it ` inside "cred**it **"); the contractor branch was unreachable for any
# string containing "contractor". Bare `officer` and bare `board` are gone
# ("chief compliance officer" still lands via `chief`), and the old
# `sales/finance` label is SPLIT into `front-office/sales` (client-facing,
# trading, brokerage) and `finance/accounting/ops` (books, tellers, clerks,
# compliance) — they carry different controls, so one bucket hid the signal.
_ROLE_FUNCTIONS: list[tuple[str, str]] = [
    (
        r"\b(contractor|consultant|vendor|third[- ]?party|supplier|outsourc\w*"
        r"|freelanc\w*|subcontractor|staffing agency)\b",
        "contractor/vendor",
    ),
    (r"\b(temp|temporary|intern|seasonal|part[- ]?time)\b", "temp/intern"),
    (
        r"\b(ceo|cfo|coo|cto|cio|ciso|cro|chief|president|founder|co-founder|vp"
        r"|vice[- ]?president|managing (?:director|partner)|general counsel"
        r"|board (?:member|chair\w*))\b"
        r"|\bexecutive\b(?!\s+assistant)"
        r"|(?<!loan )(?<!compliance )(?<!trust )\bdirector\b",
        "executive/officer",
    ),
    (
        r"(?<!account )(?<!relationship )\bmanager\b|\b(supervisor|head of|team lead)\b",
        "manager",
    ),
    (
        r"\b(engineer|developer|admin|administrator|sysadmin|dba|analyst|scientist"
        r"|technician|architect|programmer|it)\b(?!\s+assistant)",
        "technical",
    ),
    (
        r"\b(sales(?:man|woman|person)?|broker|trader|adviser|advisor"
        r"|registered representative|banker|relationship manager|agent"
        r"|account (?:executive|manager|representative)|wealth manager"
        r"|portfolio manager|underwriter)\b",
        "front-office/sales",
    ),
    (
        r"\b(accountant|bookkeeper|controller|comptroller|treasurer|teller|cashier"
        r"|clerk|adjuster|auditor|loan officer|compliance(?: officer| analyst| director)?"
        r"|trust officer|payroll|accounts payable|finance|billing)\b",
        "finance/accounting/ops",
    ),
]
_ROLE_STATES: list[tuple[str, str]] = [
    # Order matters: "former"/"fired" outranks "departing" outranks default.
    (
        r"\b(former(?:ly)?|fired|terminated|ex-employee|ex employee|dismissed|laid off)\b"
        r"|after (?:his |her |their )?(?:termination|departure|resignation)",
        "former/fired",
    ),
    (
        r"\b(departing|resign\w*|on notice|outgoing|leaving)\b"
        r"|before (?:leaving|departure|joining a competitor)",
        "departing",
    ),
    (r"\b(contractor|consultant|vendor|third[- ]?party|supplier|partner)\b", "third-party"),
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
    year_cases: Counter = Counter()
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
        year_cases[year] += 1

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
    trend_set = [t for t, _ in ranked_techs[:TREND_TECHNIQUES]]
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
    ledger = {
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
        # Trend surface. A STABLE technique set across every year (the
        # corpus-wide top TREND_TECHNIQUES), not each year's own top-N: with a
        # per-year cut, a technique disappears from a year because it ranked
        # sixth, and the chart reads that absence as a decline. With a fixed
        # set, a zero is a real zero. `cases` is the per-year contributing-case
        # count so a renderer can suppress years under the small-n floor, and
        # the "????" bucket (rows with no usable date) is reported separately
        # rather than drawn as a year.
        "by_year": {
            y: {
                "cases": year_cases[y],
                "techniques": {t: year_tech[y].get(t, 0) for t in trend_set},
            }
            for y in sorted(year_tech)
            if y != "????"
        },
        "by_year_undated_cases": year_cases.get("????", 0),
        "trend_techniques": list(trend_set),
    }
    # Derived last: the rules read the finished aggregates, so every slice and
    # every caller (API, CLI report, boot snapshot) gets the same cards.
    ledger["findings"] = derive_findings(ledger)
    ledger["finding_groups"] = group_findings(ledger["findings"])
    ledger["bottom_line"] = derive_bottom_line(ledger, ledger["findings"])
    ledger["findings_caveat"] = FINDINGS_CAVEAT if ledger["findings"] else None
    return ledger


# ---------------------------------------------------------------------------
# Derived findings
#
# The EVIDENCE page's headline cards. These used to live in web/findings.json —
# hand-authored numbers that froze on the day they were written while the
# corpus kept moving underneath them, which the "no frozen numbers, ever"
# invariant exists to prevent. Now every number is read off the finished ledger
# at request time, so the cards move with the corpus and re-derive per
# jurisdiction slice for free.
#
# What stays authored is the PROSE: titles, takeaway framing, the program
# advice, and the method caveats. That is judgment, not data — it does not go
# stale when a percentage shifts — so it lives here as templates, in the same
# class as the ITM catalog and tooling_map.json. Only numbers and subject slots
# come from the ledger.
#
# Two rules for anyone adding a rule here:
#   1. Return None rather than a weak claim. Nothing may publish a percentage
#      the small-n floor already declined to publish.
#   2. Read only fields that SURVIVE apps.search.service.evidence_ledger, which
#      pops technique_families / technique_counts / technique_hunts /
#      technique_terms / technique_behaviors. A rule built on those works in the
#      CLI report and silently vanishes from the API.
# ---------------------------------------------------------------------------

# Findings are GROUPED by the question each answers, and the page collapses
# each group. Order is the editorial decision: group one is the one open on
# load, so it holds a claim about the cases themselves.
#
# A finding states something about INSIDER BEHAVIOR OR DEFENCE. It never
# describes this corpus, this pipeline, or how to read the page — the stat
# strip, the legend and LIMITATIONS already carry all of that, and a card
# restating them is filler wearing a headline (two such cards were cut in
# 2026-08: "most cases are still allegations" and "confident language is not a
# finding of fact"). If a candidate rule's payload is a caveat, it belongs in
# LIMITATIONS.
#
# Each entry: (id, label, sub-line). A rule names its group by id; a group whose
# rules all declined is omitted entirely rather than rendered as an empty
# header advertising content that does not exist.
FINDING_GROUPS = (
    ("who", "WHO DID IT", "Which kind of person these cases name"),
    ("evidence", "WHAT PROVES A CASE", "Which records carry proven cases"),
    ("change", "WHAT'S CHANGING", "Which tactics move year to year"),
)

# Per-group cap, not a global one. Collapsed groups cost no vertical space, so
# the section stays short without silently dropping a rule that fired.
FINDINGS_PER_GROUP = 3

# Voice: short sentences, one idea each, concrete subject and verb. Read every
# line aloud before changing it — if it would sound wrong spoken to a general
# counsel, it is wrong here too.
#
# Three rules learned the hard way, after a review found the section reading
# like generated filler rather than a findings memo:
#
#   1. A recommendation must name its own finding's subject — the role, the
#      record class, the technique. A line that could sit under any card is
#      not advice, it is padding, and the reader learns to skip the block.
#   2. No rhetorical scaffolding. No "the question is not X, it is Y", no
#      "that gap is the finding". State the finding; do not announce that you
#      are stating one.
#   3. Caveats that apply to every card live in FINDINGS_CAVEAT and render
#      once. Repeating them per card trains the reader to skip exactly the
#      warnings that matter.
_ROLE_ADVICE = [
    "Apply the same escalation triggers to {label} that everyone else gets, and set"
    " them before there is a case.",
    "Give concerns about this group a reporting path that does not run through it —"
    " the audit committee, or outside counsel.",
]
_OUTSIDE_ADVICE = [
    "Name who can obtain {label} today — counsel, compliance, or the investigator —"
    " and how long it takes them.",
    "Rehearse that request once a year, the way a backup restore gets tested.",
]
_ARTIFACT_ADVICE = [
    "Ask your team two questions about {label}: can we produce it on demand, and how far back.",
    "Fund retention and legal hold for it at the level the case record says it carries.",
]
_OVER_INDEX_ADVICE = [
    "Log and review {label} accounts on the same terms as everyone else — the gap"
    " usually starts as a convenience exception.",
    "Write audit rights and evidence-preservation duties into the contract that"
    " creates the relationship, not the incident response that ends it.",
]
_TREND_ADVICE = [
    "Check whether your controls cover {label} before it appears in your own environment.",
]

# Caveats true of EVERY finding on this page. Rendered once, under the section,
# rather than repeated on each card. A card's own ``method`` carries only what
# is specific to that card.
FINDINGS_CAVEAT = (
    "How to read these. Roles, records and techniques are read out of filings by a"
    " model, not hand-audited, so treat sizes as directional. Court records are a"
    " filtered sample: they show insiders who were caught and litigated, which is"
    " exactly the population internal controls missed. Counts by year reflect how"
    " deep we have swept that year's courts as much as what happened in it."
)

# Slot the rules write instead of an ITM id. The aggregation core has no
# catalog by contract, so a rule cannot spell a technique out itself;
# attach_catalog_titles fills this at the one seam that does have the catalog,
# and the client swaps it for a button into that technique's dossier.
TECHNIQUE_SLOT = "{technique}"


def _pct(part: int, whole: int) -> int | None:
    """Whole-number percent, or None when the denominator is empty."""
    return round(100 * part / whole) if whole else None


def _finding(
    fid: str,
    *,
    group: str,
    title: str,
    stat: str,
    stat_label: str,
    takeaway: str,
    recommendations: list[str],
    method: str,
    basis: dict,
    evidence: dict | None = None,
) -> dict:
    return {
        "id": fid,
        "group": group,
        "title": title,
        "stat": stat,
        "stat_label": stat_label,
        "takeaway": takeaway,
        "recommendations": list(recommendations),
        "method": method,
        "basis": basis,
        "evidence": evidence or {},
    }


def _advice(templates: list[str], label: str) -> list[str]:
    """Fill each recommendation with its own finding's subject.

    Every template names ``{label}``. That is the point: advice that could sit
    under any card is padding, and a reader who spots one generic line stops
    reading the rest.
    """
    return [line.format(label=label) for line in templates]


def _finding_role_skew(ledger: dict, floor: int) -> dict | None:
    """Which kind of person shows up most, when the record names one."""
    roles = ledger.get("roles") or {}
    known = int(roles.get("known") or 0)
    # NB: _axis computes share_pct against enriched_cases, not role_known, so
    # every percentage this rule quotes has to use the same base or the card
    # will state a denominator the bars beside it do not use.
    cases = int(ledger.get("enriched_cases") or 0)
    for axis in ("function", "employment_state"):
        rows = [
            r
            for r in (roles.get(axis) or [])
            if r.get("label") != "unknown"
            # "current" on the employment axis is normalize_role's default fill,
            # not a signal — headlining it would report the fill.
            and not (axis == "employment_state" and r.get("label") == DEFAULTED_EMPLOYMENT_STATE)
        ]
        if len(rows) < 2 or known < floor or cases < floor:
            continue
        top, runner_up = rows[0], rows[1]
        share = top.get("share_pct")
        if share is None or top["cases"] < 1.5 * runner_up["cases"]:
            continue
        label = str(top["label"])
        return _finding(
            "role-skew",
            group="who",
            # The headline carries its own subject and its own magnitude, so it
            # still reads true with the group header deleted.
            title=f"{label.capitalize()} is named in {share}% of these cases",
            stat=f"{share}%",
            stat_label="of all cases name this group",
            takeaway=(
                f"{top['cases']} of {cases} cases name {label}; the next group, "
                f"{runner_up['label']}, appears in {runner_up['cases']}. "
                f"{top.get('adjudicated_admitted', 0)} of those are proven in court."
            ),
            recommendations=_advice(_ROLE_ADVICE, label),
            method=(
                "Counted against every case with methods, which is the base the bars"
                " below use."
                + (f" A role is named at all in {known} of them." if known < cases else "")
            ),
            basis={"n": cases, "of": cases, "floor": floor, "role_known": known},
            evidence={"kind": f"role_{axis}", "label": label},
        )
    return None


def _finding_dominant_artifact(ledger: dict, floor: int) -> dict | None:
    """Which record actually carries proven cases."""
    proven = int((ledger.get("strength_totals") or {}).get("adjudicated_admitted") or 0)
    rows = [r for r in (ledger.get("detected_by") or []) if (r.get("cases") or 0) >= floor]
    if proven < floor or len(rows) < 2:
        return None
    top, runner_up = rows[0], rows[1]
    share = top.get("adjudicated_share")
    next_share = runner_up.get("adjudicated_share")
    if share is None or next_share is None or share < next_share * 1.25:
        return None
    artifact = str(top["artifact"])
    return _finding(
        "dominant-artifact",
        group="evidence",
        title=f"{share}% of proven cases are built on {artifact}",
        stat=f"{share}%",
        stat_label="of proven cases leave this record behind",
        takeaway=(
            f"{top.get('adjudicated_admitted_cases', 0)} of {proven} proven cases turned on "
            f"{artifact}. The next record class, {runner_up['artifact']}, carries "
            f"{next_share}% — catching something and being able to prove it are"
            " different capabilities, and this is the record that does the second."
        ),
        recommendations=_advice(_ARTIFACT_ADVICE, artifact),
        method=(
            "Counts proven cases whose evidence trail touches this record class."
            " Touching means the record figured in the case evidence, not that it"
            " triggered the original detection."
        ),
        basis={"n": proven, "of": int(ledger.get("enriched_cases") or 0), "floor": floor},
        evidence={"kind": "artifact", "label": artifact},
    )


def _finding_over_index(ledger: dict, floor: int) -> dict | None:
    """A group that is small overall but large among proven cases."""
    totals = ledger.get("strength_totals") or {}
    proven = int(totals.get("adjudicated_admitted") or 0)
    known = int((ledger.get("roles") or {}).get("known") or 0)
    cases = int(ledger.get("enriched_cases") or 0)
    if proven < floor or known < floor or cases < floor:
        return None
    best = None
    for row in (ledger.get("roles") or {}).get("employment_state") or []:
        if row.get("label") in ("unknown", DEFAULTED_EMPLOYMENT_STATE):
            continue  # "current" is a default fill (see normalize_role)
        if (row.get("cases") or 0) < 1:
            continue
        strong = int(row.get("adjudicated_admitted") or 0)
        overall = _pct(int(row["cases"]), cases)
        among_proven = _pct(strong, proven)
        if overall is None or among_proven is None or overall == 0:
            continue
        if among_proven < 2 * overall:
            continue
        if best is None or among_proven > best[1]:
            best = (row, among_proven, overall, strong)
    if best is None:
        return None
    row, among_proven, overall, strong = best
    label = str(row["label"])
    return _finding(
        "proven-over-index",
        group="who",
        title=(
            f"{label.capitalize()} accounts for {overall}% of cases but "
            f"{among_proven}% of the proven ones"
        ),
        stat=f"{among_proven}%",
        stat_label="of proven cases involve this group",
        takeaway=(
            f"{strong} of the {proven} proven cases involve {label}, against "
            f"{row['cases']} of {cases} overall. Something about these cases survives to"
            " a ruling that the others do not."
        ),
        recommendations=_advice(_OVER_INDEX_ADVICE, label),
        method=(
            "Two different denominators. The overall share counts every case with"
            " methods; the proven share counts only cases a court ruled on or the"
            " insider admitted."
        ),
        basis={"n": proven, "of": cases, "floor": floor, "role_known": known},
        evidence={"kind": "role_employment_state", "label": label},
    )


def _finding_rising_technique(ledger: dict, floor: int) -> dict | None:
    """The largest year-over-year rise between two complete, well-populated years.

    Deliberately one-sided. A falling count in a query-driven corpus usually
    describes how deep we swept that year, not what insiders stopped doing, so
    there is no falling-technique card.

    The technique goes into the prose as TECHNIQUE_SLOT rather than as its ITM
    id: this module has no catalog, and a bare "MT003.002" in a headline is
    both unreadable and unclickable. attach_catalog_titles fills the slot.
    """
    by_year = ledger.get("by_year") or {}
    current = str(ledger.get("generated_at") or "")[:4]
    years = [y for y in sorted(by_year) if y != current and (by_year[y].get("cases") or 0) >= floor]
    if len(years) < 2:
        return None
    later, earlier = years[-1], years[-2]
    best_tech, best_delta = None, 0
    for tech, count in (by_year[later].get("techniques") or {}).items():
        delta = int(count) - int((by_year[earlier].get("techniques") or {}).get(tech, 0))
        if count >= floor and delta > best_delta:
            best_tech, best_delta = tech, delta
    if best_tech is None:
        return None
    now_count = by_year[later]["techniques"][best_tech]
    then_count = now_count - best_delta
    return _finding(
        "rising-technique",
        group="change",
        title=f"Cases citing {TECHNIQUE_SLOT} rose from {then_count} to {now_count} in {later}",
        stat=f"+{best_delta}",
        stat_label=f"more cases in {later} than {earlier}",
        takeaway=(
            f"{TECHNIQUE_SLOT} appears in {now_count} cases filed in {later}, against "
            f"{then_count} in {earlier}. Both years are complete and clear the reporting"
            " floor, so the direction holds for this corpus."
        ),
        recommendations=_advice(_TREND_ADVICE, TECHNIQUE_SLOT),
        method=(
            "Counted by the year the document was filed or published, not the year the"
            " incident happened."
        ),
        basis={
            "n": by_year[later]["cases"],
            "of": int(ledger.get("enriched_cases") or 0),
            "floor": floor,
        },
        evidence={"kind": "technique", "label": best_tech},
    )


def _finding_outside_telemetry(ledger: dict, floor: int) -> dict | None:
    """Proven cases resting on records no company sensor produces.

    The crosswalk has said this in a comment since it was written — external
    record classes map to almost no ITM detection because "courts often convict
    on records org telemetry never sees" — and the page has never stated it.
    It moves evidence readiness off the SOC and onto counsel and compliance,
    which is a different team than every other card here addresses.
    """
    proven = int((ledger.get("strength_totals") or {}).get("adjudicated_admitted") or 0)
    if proven < floor:
        return None
    rows = [
        r
        for r in (ledger.get("detected_by") or [])
        if r.get("artifact") in OUTSIDE_TELEMETRY_FAMILIES
        and int(r.get("adjudicated_admitted_cases") or 0) > 0
    ]
    if not rows:
        return None
    # Rank by each row's OWN proven count. Never sum the shares: their
    # denominator is the corpus-wide proven total and a case can touch several
    # classes, so they overlap and add past 100.
    rows.sort(key=lambda r: (-int(r["adjudicated_admitted_cases"]), str(r["artifact"])))
    lead = rows[0]
    hits = int(lead["adjudicated_admitted_cases"])
    if hits < floor:
        return None
    share = _pct(hits, proven)
    artifact = str(lead["artifact"])
    others = ", ".join(str(r["artifact"]) for r in rows[1:3])
    return _finding(
        "outside-telemetry",
        group="evidence",
        title=f"{share}% of proven cases turn on records your company never holds",
        stat=f"{share}%",
        stat_label="of proven cases turn on a record you cannot log",
        takeaway=(
            f"{hits} of {proven} proven cases rest on {artifact}, held by "
            f"{OUTSIDE_TELEMETRY_HOLDERS[artifact]}."
            + (f" So do cases resting on {others}." if others else "")
            + " No amount of logging produces these; counsel, a regulator, or the"
            " person's own consent does."
        ),
        recommendations=_advice(_OUTSIDE_ADVICE, artifact),
        method=(
            "A case can touch several of these classes, so the counts overlap and must"
            " not be added together. Which classes sit outside company telemetry is a"
            " short authored list. Securities cases are over-represented here because"
            " the court queries search for them by name."
        ),
        basis={"n": proven, "of": int(ledger.get("enriched_cases") or 0), "floor": floor},
        evidence={"kind": "artifact", "label": artifact},
    )


# Fixed priority. Order is the editorial decision: the honesty card first, then
# who, then what the record shows, then the caveats.
_FINDING_RULES = (
    _finding_role_skew,
    _finding_over_index,
    _finding_dominant_artifact,
    _finding_outside_telemetry,
    _finding_rising_technique,
)


def attach_catalog_titles(ledger: dict, titles: dict[str, str]) -> dict:
    """Spell out ITM ids for a human reader. Mutates and returns the ledger.

    The aggregation core is catalog-free by contract — the bare Actions runner
    has no ITM index — so every caller that HAS the catalog joins the titles on
    at this one seam: the API service and the boot-snapshot exporter both call
    it, which is what keeps the live payload and the offline first paint the
    same shape. ``titles`` maps upper-cased technique id to display title.
    """
    for tech in ledger.get("techniques") or []:
        tech["title"] = titles.get(str(tech["id"]).upper(), str(tech["id"]))
    ledger["by_year"] = {
        year: {
            "cases": bucket["cases"],
            "techniques": [
                {"id": tech, "title": titles.get(tech.upper(), tech), "cases": count}
                for tech, count in bucket["techniques"].items()
            ],
        }
        for year, bucket in (ledger.get("by_year") or {}).items()
    }
    for finding in ledger.get("findings") or []:
        ref = finding.get("evidence") or {}
        if ref.get("kind") != "technique" or not ref.get("label"):
            continue
        tech = str(ref["label"])
        # Degrade to the id on a catalog miss. An unspelled technique is worse
        # than the id; an EMPTY slot is worse than both.
        #
        # But say so. A finding that prints a bare "MT003.002" where a name
        # belongs is the exact defect this slot exists to prevent, and until
        # now that degradation was silent — indistinguishable, in the payload,
        # from a technique whose catalog title genuinely is its id. The stamp
        # lets the client mark it and lets a test catch it.
        ref["title"] = titles.get(tech.upper(), tech)
        ref["title_missing"] = tech.upper() not in titles
        fill_technique_slot(finding, ref["title"])
    # The bottom line quotes the FIRST finding's title verbatim, so when that
    # finding is a technique one the slot rides along and has to be filled from
    # the same seam, with that finding's name and no other's.
    first = (ledger.get("findings") or [None])[0]
    if ledger.get("bottom_line") and first and (first.get("evidence") or {}).get("title"):
        ledger["bottom_line"] = ledger["bottom_line"].replace(
            TECHNIQUE_SLOT, str(first["evidence"]["title"])
        )
    return ledger


def fill_technique_slot(finding: dict, name: str) -> dict:
    """Substitute TECHNIQUE_SLOT throughout one finding's prose.

    Every consumer that can spell a technique out calls this: the API service
    and the boot-snapshot exporter through attach_catalog_titles, and the CLI
    report directly with the bare id (it has no catalog, and a text report has
    nowhere to link anyway). Nothing may ship a raw slot to a reader.
    """
    for key in ("title", "takeaway", "method"):
        if isinstance(finding.get(key), str):
            finding[key] = finding[key].replace(TECHNIQUE_SLOT, name)
    finding["recommendations"] = [
        line.replace(TECHNIQUE_SLOT, name) for line in finding.get("recommendations") or []
    ]
    return finding


def derive_findings(
    ledger: dict, *, floor: int = SMALL_N_FLOOR, per_group: int = FINDINGS_PER_GROUP
) -> list[dict]:
    """Headline findings read off a finished ledger. Pure, deterministic, no I/O.

    Takes the assembled ledger dict so it works identically on a per-country
    slice, in the CLI report, and in the boot snapshot. Emits nothing at all
    when the corpus is too thin to support a claim — an empty list is the
    correct answer for a small jurisdiction, not a failure.

    Findings come back FLAT, in group order then rule order, each carrying its
    ``group`` and a global ``rank``. Grouping for display is
    ``group_findings``; a flat list keeps the CLI report, the API payload and
    any future ordering working off one sequence.
    """
    if int(ledger.get("enriched_cases") or 0) < floor:
        return []
    by_group: dict[str, list[dict]] = {}
    for rule in _FINDING_RULES:
        found = rule(ledger, floor)
        if found is None:
            continue
        bucket = by_group.setdefault(found["group"], [])
        if len(bucket) < per_group:
            bucket.append(found)
    findings = [f for gid, _, _ in FINDING_GROUPS for f in by_group.get(gid, [])]
    for rank, finding in enumerate(findings, start=1):
        finding["rank"] = rank
        # The first finding of each group leads; the rest support. The page
        # renders a lead card at full weight and a supporting one compact, so
        # five findings stop reading as five identical blocks.
        finding["weight"] = "lead" if finding is by_group[finding["group"]][0] else "supporting"
    return findings


def derive_bottom_line(
    ledger: dict, findings: list[dict], *, floor: int = SMALL_N_FLOOR
) -> str | None:
    """The one paragraph a reader gets if they read nothing else.

    A precis, never a sixth rule: every number here is already on the ledger
    totals or in a finding below it, and it introduces no claim the findings do
    not make. Returns None below the floor — a thin jurisdiction gets no
    manufactured summary.
    """
    cases = int(ledger.get("enriched_cases") or 0)
    if cases < floor or not findings:
        return None
    totals = ledger.get("strength_totals") or {}
    proven = int(totals.get("adjudicated_admitted") or 0)

    def _lower(title: str) -> str:
        # Titles open with a capital or a number; only a capitalised word may be
        # folded, or "50%" becomes "50%" and "US" becomes "uS".
        return title[0].lower() + title[1:] if title[:1].isalpha() else title

    sentences = [
        f"{cases} cases in this slice describe how an insider acted; {proven} of them"
        " are proven — a court ruled, or the person admitted it.",
        f"The clearest pattern: {_lower(findings[0]['title'])}.",
    ]
    if len(findings) > 1:
        sentences.append(f"Also: {_lower(findings[1]['title'])}.")
    return " ".join(sentences)


def group_findings(findings: list[dict]) -> list[dict]:
    """Group HEADERS in FINDING_GROUPS order, for a collapsible section.

    Metadata only — a group deliberately does NOT carry its findings. The flat
    list is the single source of truth and a consumer joins on
    ``finding["group"]``; embedding them here shipped every finding twice (44%
    of the payload measured) and, once serialized, left two independent copies
    that nothing keeps in step.

    Groups with nothing to say are omitted — an empty collapsed header
    advertises content that does not exist. Each group carries ``lead``, the
    stat of its first finding, so a COLLAPSED header still teaches something
    instead of reading as a bare label.
    """
    out = []
    for gid, label, blurb in FINDING_GROUPS:
        members = [f for f in findings if f.get("group") == gid]
        if not members:
            continue
        out.append(
            {
                "id": gid,
                "label": label,
                "blurb": blurb,
                "count": len(members),
                "lead": f"{members[0]['stat']} {members[0]['stat_label']}",
            }
        )
    return out
