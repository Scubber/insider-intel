#!/usr/bin/env python3
"""Derived-layer tagging for the Voya insider-profile study.

Reads the processed corpus (``processed/articles.jsonl``, last line wins per
link), keeps verdict-true insider cases on the current schema tier with at
least one extracted method, merges duplicate documents of one story, and
stamps every case with the tags that can be derived mechanically from the
stored record — using the corpus' own normalizers so the numbers reconcile
with the EVIDENCE page. The read layer (CERT insider type, motive, targets,
detection, impact bands, FS sub-sector) is emitted as *hints* plus empty
columns for the analyst pass; a hint is never a finding.

Outputs (in ``--out``):
  cases-tagged.jsonl      one object per case (all slices), derived tags + hints
  evidence-skeleton.csv   one row per case in the ranking slice, read-layer
                          columns empty, ``profile_primary`` empty
  counts.md               sector / jurisdiction / function x state / hint tallies

Stdlib only, so it runs on the bare Actions runner and on sparky:
  python3 analyses/voya-top10-profiles/tools/tag_cases.py corpus.jsonl --out /tmp/voya
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_core = _load("evidence_core", "shared/utils/evidence.py")
_ind = _load("industry_actor_profiles", "scripts/industry_actor_profiles.py")

V3 = 3

# --- read-layer HINTS (regex over stored text; the analyst confirms or overrides) ---

FS_SUBSECTOR_HINTS: list[tuple[str, str]] = [
    (
        r"\b(401\(k\)|403\(b\)|retirement plan|recordkeep\w*|plan sponsor|pension"
        r"|erisa|annuit)",
        "retirement/recordkeeping",
    ),
    (
        r"\b(insur\w*|underwrit\w*|claims? (adjust|process)|reinsur|life and health"
        r"|benefits? (carrier|provider))",
        "insurance",
    ),
    (
        r"\b(hedge fund|private equity|asset manag\w*|investment (adviser|advisor|management)"
        r"|portfolio manager|fund manager|mutual fund)",
        "asset-management",
    ),
    (
        r"\b(broker[- ]?dealer|brokerage|registered representative|finra|wealth manage\w*"
        r"|financial advis[eo]r|securities)",
        "broker-dealer/wealth",
    ),
    (
        r"\b(crypto\w*|bitcoin|digital asset|fintech|payments? (platform|processor)|blockchain)",
        "fintech/crypto",
    ),
    (r"\b(bank\w*|credit union|mortgage|lend\w*|loan)", "banking/lending"),
]
INSIDER_TYPE_BY_ITM: list[tuple[str, str]] = [
    ("MT017", "espionage"),
    ("IF016.004", "fraud"),
    ("IF016", "fraud"),
    ("IF044", "fraud"),
    ("PR037", "fraud"),
    ("IF022.001", "ip-theft"),
    ("ME024.001", "data-theft"),
    ("IF001", "data-or-ip-theft"),
    ("IF003", "data-or-ip-theft"),
    ("IF004", "data-or-ip-theft"),
    ("ME005", "data-or-ip-theft"),
    ("IF015", "theft"),
    ("IF014", "sabotage"),
    ("MT015", "unintentional/negligent"),
    ("MT022", "unintentional/negligent"),
]
MOTIVE_BY_ITM = {
    "MT005": "financial-gain",
    "MT005.005": "extortion",
    "MT003": "leaver/competitor",
    "MT003.002": "leaver/competitor",
    "MT003.003": "leaver/competitor",
    "MT003.005": "leaver/competitor",
    "MT021": "conflict-of-interest",
    "MT007": "revenge/grievance",
    "MT023": "revenge/grievance",
    "MT012": "coercion",
    "MT024": "recognition/ego",
    "MT017": "ideology/espionage",
    "MT015": "negligence",
    "MT022": "negligence",
}
ATTACK_HINTS: list[tuple[str, str]] = [
    (
        r"\b(usb|thumb drive|flash drive|external (hard )?drive|removable)",
        "T1052 Exfiltration over Physical Medium",
    ),
    (
        r"\b(personal (e-?mail|gmail|yahoo|hotmail)|forward\w* .{0,40}(e-?mail)|gmail)",
        "T1048 Exfiltration over Alternative Protocol (personal email)",
    ),
    (
        r"\b(dropbox|google drive|onedrive|box\.com|icloud|wetransfer|file[- ]?shar)",
        "T1567 Exfiltration to Cloud/Web Service",
    ),
    (
        r"\b(screenshot|screen capture|photograph|camera|printed?|print(ing|out))",
        "T1113/T1005 Screen capture or local collection",
    ),
    (
        r"\b(delet|wip|destroy|factory reset|purge)\w*",
        "T1485/T1070 Data destruction or indicator removal",
    ),
    (
        r"\b(shared credential|password shar|log(ged)? in as"
        r"|another employee'?s (account|credential)|valid account)",
        "T1078 Valid Accounts",
    ),
    (r"\b(mfa|multi-?factor|two-?factor|authenticator)", "T1556/T1621 MFA abuse"),
    (r"\b(impersonat|pretend|posed as|social engineer)", "T1656 Impersonation"),
    (
        r"\b(crm|salesforce|client list|customer list|database|sharepoint)",
        "T1213 Data from Information Repositories",
    ),
]
DETECTED_BY_HINTS: list[tuple[str, str]] = [
    # A named agency is the most specific signal, so it is checked first;
    # "surveillance" alone would otherwise read SEC trade surveillance as internal.
    (
        r"\b(sec|finra|fbi|law enforcement|regulator|department of justice|doj|subpoena"
        r"|grand jury)\b",
        "external-regulator/law-enforcement",
    ),
    (r"\b(self[- ]report|confess|turned (him|her|them)self)", "self-disclosure"),
    (
        r"\b(co-?worker|colleague|manager (noticed|reported)|supervisor"
        r"|employee (reported|noticed)|whistleblow)",
        "coworker-report",
    ),
    (
        r"\b(customer|client|plan sponsor|participant|investor)s? "
        r"(complain|reported|noticed|alerted|contacted)",
        "customer-complaint",
    ),
    (
        r"\b(competitor|new employer|former employer (learned|discovered))",
        "competitor/new-employer",
    ),
    (
        r"\b(audit\w*|reconcil\w*|forensic (review|examination|analysis)"
        r"|internal (review|investigation))",
        "internal-audit/review",
    ),
    (
        r"\b(dlp|data loss prevention|siem|alert\w*|monitoring (tool|system)|surveillance"
        r"|flagged by)",
        "internal-alert",
    ),
    (r"\b(discovery|deposition|litigation revealed)", "litigation-discovery"),
]
MONEY_RX = re.compile(
    r"\$\s?([\d]{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(billion|million|thousand|bn|mm|m|k)?\b",
    re.I,
)
RECORDS_RX = re.compile(
    r"\b(\d{1,3}(?:,\d{3})+|\d{3,})\s+(?:\w+\s){0,2}?"
    r"(customers?|participants?|clients?|accounts?|records?|individuals?|patients?|files?|documents?)\b",
    re.I,
)
MONEY_MULT = {
    "billion": 1e9,
    "bn": 1e9,
    "million": 1e6,
    "mm": 1e6,
    "m": 1e6,
    "thousand": 1e3,
    "k": 1e3,
}


def _first_hint(text: str, table: list[tuple[str, str]]) -> str:
    for pat, label in table:
        if re.search(pat, text, re.I):
            return label
    return "unknown"


def _all_hints(text: str, table: list[tuple[str, str]]) -> list[str]:
    out: list[str] = []
    for pat, label in table:
        if re.search(pat, text, re.I) and label not in out:
            out.append(label)
    return out


def _money_max(text: str) -> float | None:
    best = None
    for num, unit in MONEY_RX.findall(text):
        try:
            value = float(num.replace(",", ""))
        except ValueError:
            continue
        unit = (unit or "").lower()
        mult = MONEY_MULT.get(unit, 1)
        value *= mult
        if best is None or value > best:
            best = value
    return best


def _records_max(text: str) -> int | None:
    best = None
    for num, _noun in RECORDS_RX.findall(text):
        try:
            value = int(num.replace(",", ""))
        except ValueError:
            continue
        if best is None or value > best:
            best = value
    return best


def _loss_band(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 1e5:
        return "<$100k"
    if value < 1e6:
        return "$100k-$1M"
    if value < 1e7:
        return "$1M-$10M"
    return ">$10M"


def _records_band(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value < 1_000:
        return "<1k"
    if value < 100_000:
        return "1k-100k"
    return ">100k"


def _schema_version(f: dict) -> int:
    try:
        return int(f.get("schema_version") or 1)
    except (TypeError, ValueError):
        return 1


def _year(row: dict) -> str:
    legal = row.get("legal_metadata") or {}
    decision = legal.get("decision_date") if isinstance(legal, dict) else None
    for value in (decision, row.get("published")):
        if value and str(value)[:4].isdigit():
            return str(value)[:4]
    return ""


def gated_cases(rows: list[dict]) -> list[dict]:
    kept = []
    for row in rows:
        f = row.get("forensics")
        if not isinstance(f, dict) or not f.get("is_insider_case"):
            continue
        if _schema_version(f) < V3:
            continue
        if not (f.get("methods") or []):
            continue
        kept.append(row)
    return _ind.merge_cases(kept)


def tag_case(row: dict) -> dict:
    f = row.get("forensics") or {}
    methods = f.get("methods") or []
    entities = row.get("entities") or {}
    hits = entities.get("itm_hits") or []
    lexical_ids = sorted({str(h.get("id")) for h in hits if h.get("id")})
    llm_ids = sorted({str(i) for i in (f.get("candidate_technique_ids") or []) if i})
    itm_ids = sorted(set(lexical_ids) | set(llm_ids))
    function, state = _core.normalize_role(
        str(f.get("actor_role") or ""), str(f.get("actor_profile") or "")
    )
    posture = str(f.get("legal_posture") or "unknown")
    strength = _core.case_strength(methods, posture)
    families = Counter()
    channels = Counter()
    for m in methods:
        for obs in m.get("observables") or []:
            families[_core.artifact_family(str(obs.get("artifact") or ""))] += 1
            channels[str(obs.get("channel") or "")] += 1
    method_text = " ".join(
        " ".join(
            str(m.get(k) or "") for k in ("action", "target_data", "quantity", "evidence_quote")
        )
        + " "
        + " ".join(str(t) for t in (m.get("tools") or []))
        for m in methods
    )
    read_text = " ".join(
        [
            str(row.get("title") or ""),
            str(row.get("ai_summary") or ""),
            str(f.get("actor_profile") or ""),
            str(f.get("actor_role") or ""),
            str(f.get("access_vector") or ""),
            " ".join(str(x) for x in (f.get("exfil_channels") or [])),
            " ".join(str(x) for x in (f.get("motive_signals") or [])),
            str(f.get("outcome") or ""),
            method_text,
        ]
    )
    detection_text = str(f.get("detection") or "")
    money = _money_max(read_text)
    records = _records_max(read_text)
    insider_type_hints = []
    for prefix, label in INSIDER_TYPE_BY_ITM:
        hit = any(i == prefix or i.startswith(prefix + ".") for i in itm_ids)
        if hit and label not in insider_type_hints:
            insider_type_hints.append(label)
    motive_hints = sorted({MOTIVE_BY_ITM[i] for i in itm_ids if i in MOTIVE_BY_ITM})
    return {
        "case_id": row.get("link"),
        "title": row.get("title"),
        "source_id": row.get("source_id"),
        "channel": row.get("channel"),
        "published": row.get("published"),
        "year": _year(row),
        "country": _core.resolve_country(row.get("source_id"), row.get("legal_metadata")) or "none",
        "industry": _core.resolve_industry(f),
        "actor_employer_sector": _core.resolve_actor_employer_sector(f),
        "function": function,
        "employment_state": state,
        "employment_state_is_default_fill": state == _core.DEFAULTED_EMPLOYMENT_STATE,
        "legal_posture": posture,
        "strength": strength,
        "confidence": f.get("confidence"),
        "model": f.get("model"),
        "itm_ids": itm_ids,
        "itm_ids_lexical": lexical_ids,
        "itm_ids_llm": llm_ids,
        "motive_ids": [i for i in itm_ids if i.startswith("MT")],
        "method_count": len(methods),
        "claim_statuses": sorted({str(m.get("claim_status") or "unclear") for m in methods}),
        "evidence_families": dict(families),
        "observable_channels": dict(channels),
        "exfil_channels": f.get("exfil_channels") or [],
        "tool_mentions": f.get("tool_mentions") or [],
        "actor_role": f.get("actor_role"),
        "actor_profile": f.get("actor_profile"),
        "access_vector": f.get("access_vector"),
        "motive_signals": f.get("motive_signals") or [],
        "timeframe": f.get("timeframe"),
        "detection": f.get("detection"),
        "outcome": f.get("outcome"),
        "ai_summary": row.get("ai_summary"),
        "methods": methods,
        # --- hints for the read layer (never findings) ---
        "hint_fs_subsector": _first_hint(read_text, FS_SUBSECTOR_HINTS),
        "hint_insider_type": insider_type_hints or ["unknown"],
        "hint_motive": motive_hints or ["unknown"],
        "hint_attack": _all_hints(read_text, ATTACK_HINTS),
        "hint_detected_by": (
            _first_hint(detection_text, DETECTED_BY_HINTS) if detection_text else "unknown"
        ),
        "hint_loss_usd_max": money,
        "hint_loss_band": _loss_band(money),
        "hint_records_max": records,
        "hint_records_band": _records_band(records),
    }


CSV_COLUMNS = [
    "case_id",
    "title",
    "country",
    "industry",
    "actor_employer_sector",
    "year",
    "function",
    "employment_state",
    "employment_state_is_default_fill",
    "legal_posture",
    "strength",
    "itm_ids",
    "motive_ids",
    "hint_fs_subsector",
    "hint_insider_type",
    "hint_motive",
    "hint_attack",
    "hint_detected_by",
    "hint_loss_band",
    "hint_records_band",
    # read layer — filled by the analyst pass
    "fs_subsector",
    "fs_role",
    "access_level",
    "insider_type",
    "motive",
    "target_assets",
    "attack_ids",
    "non_technical_method",
    "precursors",
    "detected_by",
    "time_to_detection",
    "loss_usd_band",
    "records_band",
    "regulatory_action",
    "profile_primary",
    "profile_secondary",
    "mapping_note",
    "verified_against_record",
]


def write_outputs(cases: list[dict], out: Path, *, industry: str, country: str) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "cases-tagged.jsonl").open("w", encoding="utf-8") as fh:
        for c in cases:
            fh.write(json.dumps(c, ensure_ascii=False, default=str) + "\n")
    in_slice = [
        c
        for c in cases
        if (industry in ("all", "") or c["industry"] == industry)
        and (country in ("all", "") or c["country"] == country)
    ]
    with (out / "evidence-skeleton.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for c in in_slice:
            rec = dict(c)
            for k in ("itm_ids", "motive_ids", "hint_insider_type", "hint_motive", "hint_attack"):
                rec[k] = "|".join(rec.get(k) or [])
            w.writerow(rec)
    counts = {
        "cases_total": len(cases),
        "by_industry": Counter(c["industry"] for c in cases),
        "by_country_in_industry": Counter(
            c["country"] for c in cases if industry in ("all", "") or c["industry"] == industry
        ),
        "slice": {"industry": industry, "country": country, "cases": len(in_slice)},
        "by_function_state": Counter(
            f"{c['function']} / {c['employment_state']}" for c in in_slice
        ),
        "by_strength": Counter(c["strength"] for c in in_slice),
        "by_hint_subsector": Counter(c["hint_fs_subsector"] for c in in_slice),
        "by_hint_insider_type": Counter(t for c in in_slice for t in c["hint_insider_type"]),
        "by_hint_detected_by": Counter(c["hint_detected_by"] for c in in_slice),
        "by_year": Counter(c["year"] or "unknown" for c in in_slice),
    }
    lines = [f"# Derived-layer counts (slice: industry={industry}, country={country})", ""]
    lines.append(f"Gated cases (all slices): {counts['cases_total']}  ·  in slice: {len(in_slice)}")
    for title, key in [
        ("Victim industry (all gated cases)", "by_industry"),
        (f"Jurisdiction within industry={industry}", "by_country_in_industry"),
        ("Function × employment state (slice)", "by_function_state"),
        ("Strength (slice)", "by_strength"),
        ("FS sub-sector HINT (slice)", "by_hint_subsector"),
        ("Insider-type HINT (slice)", "by_hint_insider_type"),
        ("Detected-by HINT from forensics.detection (slice)", "by_hint_detected_by"),
        ("Filing/published year (slice)", "by_year"),
    ]:
        lines += ["", f"## {title}", "", "| Value | Cases |", "|---|---:|"]
        for value, n in sorted(counts[key].items(), key=lambda kv: (-kv[1], str(kv[0]))):
            lines.append(f"| {value} | {n} |")
    (out / "counts.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("corpus", help="processed/articles.jsonl")
    ap.add_argument("--out", default="voya-tagging", help="output directory")
    ap.add_argument(
        "--industry",
        default="financial-services",
        help="victim-sector slice for the CSV (all = every sector)",
    )
    ap.add_argument(
        "--country", default="US", help="jurisdiction slice for the CSV (all = every jurisdiction)"
    )
    args = ap.parse_args(argv)
    rows = _core.collapse_rows_by_link(_core.iter_jsonl_rows(args.corpus))
    cases = [tag_case(r) for r in gated_cases(rows)]
    counts = write_outputs(cases, Path(args.out), industry=args.industry, country=args.country)
    print(
        f"gated cases: {counts['cases_total']}; in slice: {counts['slice']['cases']}; "
        f"wrote {args.out}/"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
