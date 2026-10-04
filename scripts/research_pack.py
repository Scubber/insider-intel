#!/usr/bin/env python3
"""Research pack: the corpus material a briefing is written from, per topic.

Read-only research report (dispatched by .github/workflows/research-pack.yml,
runnable locally). Slices the processed corpus by a topic spec — a regex over
the case text, ITM technique ids, jurisdiction, victim industry, posture,
filing year — and prints what a briefing author needs:

- the basis funnel (rows → deduped → verdict-true with methods → matched);
- counts by posture, year, jurisdiction, channel, industry, actor function ×
  employment state, access vector, motive, exfil channel, technique;
- how the case was detected (authored regex families over the enricher's
  ``detection`` text), tools named by role (caught / bypassed / misused /
  traced), and the quantities the record states;
- up to N case cards (title, link, posture, one analyst-note line, methods,
  detection, outcome) so the author can cite receipts.

Everything here is already public product: the API serves the same rows on
/articles, /search and /evidence/*. The pack still passes every free-text
field through ``redact()`` (email local parts → <redacted>@domain, the same
rule as scripts/email_domain_scan.py) and the tests assert a seeded local
part never survives. Roles, never individuals: no persona or entity
resolution; adjudicated, alleged and reported are reported separately and
never summed into one "cases" figure without the split beside it.

Stdlib only — the bare Actions runner has no pydantic. The aggregation core
(shared/utils/evidence.py) is loaded as a bare file, same as evidence_ledger.py.

Usage: research_pack.py CORPUS.jsonl --query "trade secret|misappropriat"
       [--technique IF016 ...] [--country US] [--industry financial-services]
       [--posture any|adjudicated|alleged] [--year-from 2015] [--cases 15]
       [--scope full|meta] [--include-unverdicted] [--json out.json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_CORE_PATH = Path(__file__).resolve().parent.parent / "shared" / "utils" / "evidence.py"
_spec = importlib.util.spec_from_file_location("evidence_core", _CORE_PATH)
_core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core)

SMALL_N_FLOOR = _core.SMALL_N_FLOOR
STRENGTH_RANK = _core.STRENGTH_RANK
V3_SCHEMA = 3

EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@([A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,})"
)

# How the case came to light — authored families over the enricher's free-text
# ``detection`` field. First match wins; order is specific → general.
DETECTION_FAMILIES: tuple[tuple[str, str], ...] = (
    (
        "forensic exam after departure",
        r"forensic|imag(?:e|ing) of|examin(?:ed|ation) of .*(laptop|device|computer)"
        r"|post-?(?:departure|resignation|termination) review",
    ),
    (
        "monitoring or DLP alert",
        r"\bdlp\b|data loss|alert|flagged|monitoring (?:tool|system|software)|siem|ueba|anomal",
    ),
    (
        "audit or log review",
        r"audit|log review|reviewed? (?:the )?(?:access|email|download|print|badge) "
        r"(?:logs?|records?)|access logs?|download logs?",
    ),
    (
        "colleague or manager report",
        r"co-?worker|colleague|employee reported|manager (?:noticed|reported)|supervisor"
        r"|tip(?:ped)?\b|whistle",
    ),
    (
        "customer, partner or competitor notice",
        r"customer|client|competitor|vendor|partner|new employer|recruit",
    ),
    ("the insider disclosed it", r"admitted|confess|self-?report|disclosed (?:to|that)"),
    (
        "law enforcement or regulator",
        r"\bfbi\b|police|law enforcement|regulator|\bsec\b|investigat(?:ion|ors) by",
    ),
    (
        "public exposure or leak",
        r"leak|posted|published|public(?:ly)? (?:available|accessible)|dark ?web|pastebin|github",
    ),
)
_DETECTION_RX = [(label, re.compile(rx, re.I)) for label, rx in DETECTION_FAMILIES]

STRENGTH_ORDER = ("adjudicated/admitted", "alleged", "reported/unclear")


def redact(text) -> str:
    """Single choke point: every free-text field printed passes through here."""
    s = str(text or "")
    return EMAIL_RE.sub(lambda m: f"<redacted>@{m.group(1).lower()}", s)


def _one_line(text, limit: int) -> str:
    s = " ".join(redact(text).split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def detection_family(text) -> str:
    s = str(text or "").strip()
    if not s:
        return "not stated"
    for label, rx in _DETECTION_RX:
        if rx.search(s):
            return label
    return "other / unclassified"


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _row_text(row: dict, f: dict, scope: str) -> str:
    parts = [
        row.get("title"),
        row.get("ai_summary"),
        f.get("actor_profile"),
        f.get("detection"),
        f.get("outcome"),
        " ".join(str(t) for t in (f.get("hunt_terms") or [])),
        " ".join(
            str(m.get("action") or "") for m in (f.get("methods") or []) if isinstance(m, dict)
        ),
        " ".join(
            str(m.get("target_data") or "") for m in (f.get("methods") or []) if isinstance(m, dict)
        ),
    ]
    if scope == "full":
        parts.append(row.get("clean_text"))
    return " \n ".join(str(p) for p in parts if p)


def select_rows(
    rows,
    *,
    query: str,
    techniques: list[str],
    country: str = "all",
    industry: str = "all",
    posture: str = "any",
    year_from: int | None = None,
    scope: str = "full",
    min_hits: int = 1,
) -> dict:
    """Return the funnel + matched verdict-true rows (and matched others, counted)."""
    rx = re.compile(query, re.I) if query and query != "." else None
    want_tech = {t.strip().upper() for t in techniques if t.strip()}
    funnel = Counter()
    matched: list[dict] = []
    matched_other = Counter()  # non-verdict-true matches, by reason (context only)
    for row in rows:
        if not isinstance(row, dict):
            continue
        funnel["rows"] += 1
        f = row.get("forensics")
        f = f if isinstance(f, dict) else {}
        if f:
            funnel["with_forensics"] += 1
        if country not in ("", "*", "all"):
            if _core.resolve_country(row.get("source_id"), row.get("legal_metadata")) != country:
                continue
        if industry not in ("", "*", "all"):
            if _core.resolve_industry(f) != industry:
                continue
        year = str(row.get("published") or "")[:4]
        if year_from and (not year.isdigit() or int(year) < year_from):
            continue
        funnel["in_slice"] += 1
        if want_tech:
            ids = {str(t).upper() for t in (f.get("candidate_technique_ids") or [])}
            if not (ids & want_tech):
                continue
        if rx is not None:
            hits = len(rx.findall(_row_text(row, f, scope)))
            if hits < min_hits:
                continue
        funnel["matched_any"] += 1
        methods = [m for m in (f.get("methods") or []) if isinstance(m, dict)]
        verdict = f.get("is_insider_case")
        if not methods:
            matched_other["no extracted methods (un-enriched or floor)"] += 1
            continue
        if verdict is False:
            matched_other["enricher adjudicated NOT an insider case"] += 1
            continue
        if verdict is not True:
            matched_other["no verdict stored"] += 1
            continue
        strength = _core.case_strength(methods, str(f.get("legal_posture") or ""))
        if posture == "adjudicated" and strength != "adjudicated/admitted":
            continue
        if posture == "alleged" and strength != "alleged":
            continue
        matched.append(row)
    funnel["matched_cases"] = len(matched)
    return {"funnel": dict(funnel), "cases": matched, "matched_other": dict(matched_other)}


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _year(row: dict) -> str:
    y = str(row.get("published") or "")[:4]
    return y if y.isdigit() else "????"


def aggregate(cases: list[dict]) -> dict:
    strength = Counter()
    posture = Counter()
    years = Counter()
    countries = Counter()
    channels = Counter()
    industries = Counter()
    employer = Counter()
    not_asked = 0
    role = Counter()
    access = Counter()
    motives = Counter()
    exfil = Counter()
    techniques = Counter()
    detection = Counter()
    tools: dict[str, Counter] = defaultdict(Counter)
    quantities = Counter()
    insider_type = Counter()
    use_cases = Counter()
    for row in cases:
        f = row["forensics"]
        methods = [m for m in (f.get("methods") or []) if isinstance(m, dict)]
        s = _core.case_strength(methods, str(f.get("legal_posture") or ""))
        strength[s] += 1
        posture[str(f.get("legal_posture") or "unknown").strip().lower() or "unknown"] += 1
        years[_year(row)] += 1
        countries[
            _core.resolve_country(row.get("source_id"), row.get("legal_metadata"))
            or "none (not a court lane)"
        ] += 1
        channels[str(row.get("channel") or "unknown")] += 1
        if int(f.get("schema_version") or 0) >= V3_SCHEMA:
            industries[_core.resolve_industry(f)] += 1
            employer[_core.resolve_actor_employer_sector(f)] += 1
        else:
            not_asked += 1
        fn, state = _core.normalize_role(
            str(f.get("actor_role") or ""), str(f.get("actor_profile") or "")
        )
        role[(fn, state)] += 1
        access[str(f.get("access_vector") or "not stated").strip().lower()] += 1
        for m in f.get("motive_signals") or []:
            motives[str(m).strip().lower()[:40]] += 1
        for c in f.get("exfil_channels") or []:
            exfil[str(c).strip().lower()[:40]] += 1
        for t in f.get("candidate_technique_ids") or []:
            techniques[str(t).upper()] += 1
        detection[detection_family(f.get("detection"))] += 1
        for tm in f.get("tool_mentions") or []:
            if isinstance(tm, dict) and tm.get("name"):
                tools[str(tm.get("role") or "unknown")][str(tm["name"]).strip()[:40]] += 1
        for m in methods:
            q = str(m.get("quantity") or "").strip()
            if q:
                quantities[_one_line(q, 60)] += 1
        if row.get("insider_type"):
            insider_type[str(row["insider_type"])] += 1
        for u in row.get("use_cases") or []:
            use_cases[str(u)] += 1
    return {
        "strength": {k: strength.get(k, 0) for k in STRENGTH_ORDER},
        "posture": posture.most_common(10),
        "years": sorted(years.items()),
        "countries": countries.most_common(),
        "channels": channels.most_common(),
        "industry": industries.most_common(),
        "employer_sector": employer.most_common(),
        "industry_not_asked": not_asked,
        "roles": sorted(role.items(), key=lambda kv: -kv[1])[:15],
        "access_vector": access.most_common(8),
        "motives": motives.most_common(10),
        "exfil_channels": exfil.most_common(10),
        "techniques": techniques.most_common(15),
        "detection": detection.most_common(),
        "tools": {role: c.most_common(10) for role, c in sorted(tools.items())},
        "quantities": quantities.most_common(12),
        "insider_type": insider_type.most_common(),
        "use_cases": use_cases.most_common(),
    }


def case_cards(cases: list[dict], n: int) -> list[dict]:
    def key(row):
        f = row["forensics"]
        methods = [m for m in (f.get("methods") or []) if isinstance(m, dict)]
        s = _core.case_strength(methods, str(f.get("legal_posture") or ""))
        return (
            -STRENGTH_RANK[s],
            -(float(f.get("confidence") or 0.0)),
            str(row.get("published") or ""),
        )

    cards = []
    for row in sorted(cases, key=key)[:n]:
        f = row["forensics"]
        methods = [m for m in (f.get("methods") or []) if isinstance(m, dict)]
        fn, state = _core.normalize_role(
            str(f.get("actor_role") or ""), str(f.get("actor_profile") or "")
        )
        cards.append(
            {
                "title": _one_line(row.get("title") or row.get("link"), 110),
                "link": str(row.get("link") or ""),
                "year": _year(row),
                "country": _core.resolve_country(row.get("source_id"), row.get("legal_metadata"))
                or "—",
                "posture": str(f.get("legal_posture") or "unknown").strip().lower() or "unknown",
                "strength": _core.case_strength(methods, str(f.get("legal_posture") or "")),
                "industry": _core.resolve_industry(f)
                if int(f.get("schema_version") or 0) >= V3_SCHEMA
                else "not asked",
                "actor": f"{fn} / {state}",
                "summary": _one_line(row.get("ai_summary") or "", 320),
                "methods": [_one_line(m.get("action"), 120) for m in methods[:3]],
                "detection": _one_line(f.get("detection") or "", 160),
                "outcome": _one_line(f.get("outcome") or "", 160),
            }
        )
    return cards


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _pct(part: int, whole: int) -> str:
    if whole < SMALL_N_FLOOR or whole == 0:
        return ""
    return f" ({round(100 * part / whole)}%)"


def render_markdown(pack: dict) -> str:
    fu, agg, spec = pack["funnel"], pack["aggregate"], pack["spec"]
    n = fu.get("matched_cases", 0)
    L = ["# insider-intel research pack", ""]
    L.append(f"Generated {pack['generated_at']} · AS OF the corpus as pulled at that time")
    L.append(
        "Topic spec: "
        + " · ".join(f"{k}={v}" for k, v in spec.items() if v not in (None, "", [], "all", "any"))
    )
    L.append("")
    L.append("## Basis funnel")
    L.append(
        f"- corpus rows {fu.get('rows', 0)} (deduped, last line wins) · with forensics "
        f"{fu.get('with_forensics', 0)} · in slice {fu.get('in_slice', 0)} · "
        "text/technique matches "
        f"{fu.get('matched_any', 0)} · **verdict-true insider cases with methods: {n}**"
    )
    for reason, c in sorted(pack["matched_other"].items()):
        L.append(f"- matched but excluded: {c} — {reason}")
    if n < SMALL_N_FLOOR:
        L.append(
            f"- SMALL N: {n} cases is under the {SMALL_N_FLOOR}-case floor; no percentages below"
        )
    L.append("")
    L.append("## Posture (never summed)")
    for k in STRENGTH_ORDER:
        L.append(f"- {agg['strength'][k]:4d}{_pct(agg['strength'][k], n)}  {k}")
    L.append("- legal posture labels: " + ", ".join(f"{p}×{c}" for p, c in agg["posture"]))
    L.append("")
    L.append("## By year filed / published")
    L.append("- " + ", ".join(f"{y}: {c}" for y, c in agg["years"]))
    L.append("")
    L.append("## Jurisdiction and channel")
    L.append("- jurisdiction: " + ", ".join(f"{k}×{c}" for k, c in agg["countries"]))
    L.append("- channel: " + ", ".join(f"{k}×{c}" for k, c in agg["channels"]))
    L.append("")
    L.append("## Industry (schema v3 rows only)")
    L.append("- victim sector: " + (", ".join(f"{k}×{c}" for k, c in agg["industry"]) or "none"))
    L.append(
        "- insider's employer sector: "
        + (", ".join(f"{k}×{c}" for k, c in agg["employer_sector"]) or "none")
    )
    L.append(f"- not asked (pre-v3 enrichment): {agg['industry_not_asked']}")
    L.append("")
    L.append("## Actor function × employment state (roles, never individuals)")
    for (fn, st), c in agg["roles"]:
        L.append(f"- {c:4d}{_pct(c, n)}  {fn} / {st}")
    L.append("")
    L.append("## Access, motive, channel")
    L.append("- access vector: " + ", ".join(f"{k}×{c}" for k, c in agg["access_vector"]))
    L.append(
        "- motive signals: " + (", ".join(f"{k}×{c}" for k, c in agg["motives"]) or "none stated")
    )
    L.append(
        "- exfil channels: "
        + (", ".join(f"{k}×{c}" for k, c in agg["exfil_channels"]) or "none stated")
    )
    if agg["insider_type"]:
        L.append(
            "- insider type (classifier): " + ", ".join(f"{k}×{c}" for k, c in agg["insider_type"])
        )
    if agg["use_cases"]:
        L.append("- use cases (classifier): " + ", ".join(f"{k}×{c}" for k, c in agg["use_cases"]))
    L.append("")
    L.append("## ITM techniques in the slice")
    L.append("- " + (", ".join(f"{t}×{c}" for t, c in agg["techniques"]) or "none"))
    L.append("")
    L.append("## How the case came to light (enricher's detection text, authored families)")
    for k, c in agg["detection"]:
        L.append(f"- {c:4d}{_pct(c, n)}  {k}")
    L.append("")
    L.append("## Tools named, by role in the record")
    if agg["tools"]:
        for role, items in agg["tools"].items():
            L.append(f"- {role}: " + ", ".join(f"{k}×{c}" for k, c in items))
    else:
        L.append("- none named")
    L.append("")
    L.append("## Quantities the record states")
    L.append(
        "- " + ("; ".join(f"{q} (×{c})" if c > 1 else q for q, c in agg["quantities"]) or "none")
    )
    L.append("")
    L.append(f"## Case cards (strongest posture first, {len(pack['cards'])} of {n})")
    for i, c in enumerate(pack["cards"], 1):
        L.append("")
        L.append(f"### {i}. {c['title']}")
        L.append(
            f"- {c['year']} · {c['country']} · posture {c['posture']} → {c['strength']} · "
            f"industry {c['industry']} · actor {c['actor']}"
        )
        L.append(f"- link: {c['link']}")
        if c["summary"]:
            L.append(f"- note: {c['summary']}")
        for m in c["methods"]:
            L.append(f"- method: {m}")
        if c["detection"]:
            L.append(f"- detected: {c['detection']}")
        if c["outcome"]:
            L.append(f"- outcome: {c['outcome']}")
    L.append("")
    L.append("## Reading rules")
    L.append(
        "- Counts are cases, each the current row for its link. Adjudicated/admitted, alleged and "
        "reported/unclear are separate findings; never add them into one number without the split. "
        "Jurisdiction is the source court, never the actor's nationality. Industry is only known "
        "on "
        "schema-v3 rows. Detection families are regex over the enricher's prose: a hint, not a "
        "finding. Under the small-n floor, quote counts, not percentages."
    )
    return "\n".join(L) + "\n"


def build_pack(rows, *, spec: dict, cases_n: int, now: dt.datetime | None = None) -> dict:
    sel = select_rows(
        rows,
        query=spec["query"],
        techniques=spec["techniques"],
        country=spec["country"],
        industry=spec["industry"],
        posture=spec["posture"],
        year_from=spec["year_from"],
        scope=spec["scope"],
        min_hits=spec["min_hits"],
    )
    stamp = (now or dt.datetime.now(dt.UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "generated_at": stamp,
        "spec": spec,
        "funnel": sel["funnel"],
        "matched_other": sel["matched_other"],
        "aggregate": aggregate(sel["cases"]),
        "cards": case_cards(sel["cases"], cases_n),
    }


def _parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("corpus")
    ap.add_argument("--query", default=".", help="regex over case text ('.' = everything)")
    ap.add_argument("--technique", action="append", default=[], help="ITM id (repeatable)")
    ap.add_argument("--country", default="all")
    ap.add_argument("--industry", default="all")
    ap.add_argument("--posture", choices=("any", "adjudicated", "alleged"), default="any")
    ap.add_argument("--year-from", type=int, default=None)
    ap.add_argument(
        "--scope", choices=("full", "meta"), default="full", help="full = include clean_text"
    )
    ap.add_argument("--min-hits", type=int, default=1)
    ap.add_argument("--cases", type=int, default=15)
    ap.add_argument("--json", default=None)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    a = _parse_args(argv)
    spec = {
        "query": a.query,
        "techniques": [t.upper() for t in a.technique],
        "country": (a.country or "all").strip().upper()
        if a.country.strip().lower() not in ("", "*", "all")
        else "all",
        "industry": (a.industry or "all").strip().lower(),
        "posture": a.posture,
        "year_from": a.year_from,
        "scope": a.scope,
        "min_hits": a.min_hits,
    }
    rows = _core.collapse_rows_by_link(_core.iter_jsonl_rows(a.corpus))
    pack = build_pack(rows, spec=spec, cases_n=a.cases)
    sys.stdout.write(render_markdown(pack))
    if a.json:
        Path(a.json).write_text(json.dumps(pack, indent=1, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
