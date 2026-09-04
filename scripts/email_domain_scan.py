"""Domain-level tally of email addresses quoted in stored case documents.

Read-only research scan (dispatched by .github/workflows/corpus-emailscan.yml,
runnable locally): extract every literal email address from a corpus JSONL,
classify each hit's CONTEXT (exfil-destination vs counsel/service vs incidental
mention), and report domain-level tallies plus redacted evidence snippets.

Two rules this script exists to enforce, not just follow:

- ROLES, NEVER INDIVIDUALS. No output path — report, JSON, or log — ever
  prints a local part. Domains are tallied; local parts reduce to shape
  classes; every snippet passes through redact(), the single choke point that
  rewrites addresses to <redacted>@domain. tests/test_email_domain_scan.py
  asserts a seeded local part cannot appear in any output.
- THE COUNSEL CONFOUND. Court filings are full of attorney and ECF service
  addresses that say nothing about the conduct. Hits are context-classified
  BEFORE tallying, so "gmail.com x40" can be read as destinations, not
  signature blocks.

Stdlib only — the bare Actions runner has no pydantic (same contract as
count_stale_filings.py). Never walks enrichment_history: every generation
duplicates the selected forensics and would multi-count.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# The evidence core is loaded as a bare FILE (same trick as evidence_ledger.py):
# the shared.utils package __init__ pulls in pydantic, which the Actions
# runner does not have. The core is pure stdlib by contract.
_CORE_PATH = Path(__file__).resolve().parent.parent / "shared" / "utils" / "evidence.py"
_spec = importlib.util.spec_from_file_location("evidence_core", _CORE_PATH)
_core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}")

# Domains that are court/e-filing infrastructure regardless of surrounding text.
_LEGAL_INFRA = ("uscourts.gov", "courtlistener.com", "pacer.gov", "pacer.uscourts.gov", "ecf.gov")

_CONSUMER_WEBMAIL = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.uk",
    "yahoo.co.in",
    "ymail.com",
    "rocketmail.com",
    "hotmail.com",
    "hotmail.co.uk",
    "outlook.com",
    "live.com",
    "msn.com",
    "aol.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "comcast.net",
    "att.net",
    "verizon.net",
    "sbcglobal.net",
    "bellsouth.net",
    "cox.net",
    "charter.net",
    "earthlink.net",
    "gmx.com",
    "gmx.net",
    "mail.com",
    "zoho.com",
    "qq.com",
    "163.com",
    "126.com",
    "sina.com",
    "rediffmail.com",
    "mail.ru",
    "yandex.com",
    "yandex.ru",
    "web.de",
    "t-online.de",
}
_PRIVACY_ENCRYPTED = {
    "proton.me",
    "protonmail.com",
    "protonmail.ch",
    "pm.me",
    "tutanota.com",
    "tuta.io",
    "tuta.com",
    "hushmail.com",
    "mailfence.com",
    "skiff.com",
    "countermail.com",
}
_DISPOSABLE = {
    "mailinator.com",
    "guerrillamail.com",
    "10minutemail.com",
    "temp-mail.org",
    "tempmail.com",
    "sharklasers.com",
    "yopmail.com",
    "getnada.com",
    "trashmail.com",
    "throwawaymail.com",
    "maildrop.cc",
}

_ROLE_LOCALS = {
    "info",
    "admin",
    "administrator",
    "sales",
    "support",
    "help",
    "hr",
    "legal",
    "office",
    "contact",
    "team",
    "billing",
    "accounts",
    "noreply",
    "no-reply",
    "donotreply",
    "notifications",
    "service",
    "webmaster",
    "postmaster",
    "careers",
    "jobs",
    "press",
}

# Signature-block / certificate-of-service vocabulary. A hit whose window
# matches reads as counsel or court plumbing, not conduct.
_COUNSEL_RE = re.compile(
    r"attorneys? for|counsel (?:for|of record)|law (?:firm|office|group)|\bLLP\b|\bPLLC\b"
    r"|\bEsq\b|pro hac vice|\bECF\b|electronically filed|certificate of service"
    r"|/s/|\bs/\s|bar (?:no|number)|telephone[:\s]|facsimile|\bfax\b|clerk of (?:the )?court"
    r"|respectfully submitted|cm/ecf|notice of (?:appearance|electronic filing)"
    r"|creditor matrix|mailing matrix|notice will be sent|service list"
    r"|served (?:via|by|upon)|parties in interest|(?:state )?bar association|state bar",
    re.IGNORECASE,
)

# A window packed with distinct addresses is an e-service distribution list,
# creditor matrix, or party contact block — plumbing, not conduct. Hand review
# of the 2026-08 run measured these lists as the main source of counsel
# leakage into the exfil class (they carry "sent"/"copied" vocabulary).
_SERVICE_LIST_MIN = 3

# Conduct vocabulary: the address is where something was SENT, or it is called
# a personal account. Applied to the same window.
_EXFIL_RE = re.compile(
    r"forward(?:ed|ing|s)?|\bsent\b|\bsends\b|\bsending\b|e-?mail(?:ed|ing)?\s|transferr?(?:ed|ing)"
    r"|\bcopied\b|download(?:ed|ing)?|export(?:ed|ing)?|upload(?:ed|ing)?|\bbcc\b|blind cop"
    r"|auto-?forward|(?:personal|private|home|non-?company|his own|her own|their own)\s"
    r"(?:e-?mail|g?mail|yahoo|account|address|inbox)",
    re.IGNORECASE,
)

_AUTOMATION_FLAGS = (
    (
        "auto_forward",
        re.compile(r"auto-?forward|forwarding rule|mail(?:box)? rule|inbox rule", re.I),
    ),
    ("scripted", re.compile(r"script(?:ed)?|automat(?:ed|ically)|scheduled task|cron", re.I)),
    ("bcc", re.compile(r"\bbcc\b|blind cop", re.I)),
    (
        "bulk_volume",
        re.compile(r"\b\d{2,}(?:,\d{3})*\s+(?:e-?mails|messages|documents|files)\b", re.I),
    ),
    (
        "recurring",
        re.compile(r"daily basis|every (?:day|night|week)|over (?:a period|several months)", re.I),
    ),
)

_TRIM = ".,;:()<>[]{}\"'`’”»"


def redact(text: str) -> str:
    """THE choke point: every output string passes here. Local parts never survive."""
    return EMAIL_RE.sub(lambda m: "<redacted>@" + m.group(0).rsplit("@", 1)[1].lower(), text or "")


def _clean(raw: str) -> tuple[str, str] | None:
    addr = raw.strip(_TRIM)
    if addr.lower().startswith("mailto:"):
        addr = addr[7:]
    if "@" not in addr:
        return None
    local, _, domain = addr.rpartition("@")
    local, domain = local.strip(_TRIM), domain.strip(_TRIM).lower().rstrip(".")
    if not (1 <= len(local) <= 64) or ".." in domain or "." not in domain:
        return None
    return local, domain


def domain_category(domain: str) -> str:
    if domain in _CONSUMER_WEBMAIL:
        return "consumer_webmail"
    if domain in _PRIVACY_ENCRYPTED:
        return "privacy_encrypted"
    if domain in _DISPOSABLE:
        return "disposable"
    if domain.endswith(".gov") or any(
        domain == d or domain.endswith("." + d) for d in _LEGAL_INFRA
    ):
        return "gov_legal_infra"
    if domain.endswith(".edu"):
        return "edu"
    return "corporate_other"


def local_shape(local: str) -> str:
    """Shape class only — the local part itself is never emitted."""
    low = local.lower()
    if low in _ROLE_LOCALS:
        return "role_account"
    if re.fullmatch(r"[a-z]+(?:[._-][a-z]+)+", low) or re.fullmatch(r"[a-z]{3,15}", low):
        return "name_like"
    if re.fullmatch(r"[a-z]+(?:[._-][a-z]+)*[._-]?\d{1,4}", low):
        return "handle_digits"
    digits = sum(c.isdigit() for c in low)
    vowels = sum(c in "aeiou" for c in low)
    if len(low) >= 10 and (digits >= 4 or vowels <= len(low) // 6):
        return "random_machine"
    return "other"


def _nearest(rx: re.Pattern, window: str, hit_at: int) -> int | None:
    """Distance from the hit to the closest match of rx, or None."""
    best = None
    for m in rx.finditer(window):
        mid = (m.start() + m.end()) // 2
        d = abs(mid - hit_at)
        if best is None or d < best:
            best = d
    return best


def _looks_like_service_list(window: str) -> bool:
    """True when the window holds >= _SERVICE_LIST_MIN distinct addresses."""
    addrs = set()
    for m in EMAIL_RE.finditer(window):
        cleaned = _clean(m.group(0))
        if cleaned:
            addrs.add(cleaned[0].lower() + "@" + cleaned[1])
        if len(addrs) >= _SERVICE_LIST_MIN:
            return True
    return False


def classify_window(
    window: str, domain: str, from_forensics: bool, hit_at: int | None = None
) -> str:
    if domain_category(domain) == "gov_legal_infra":
        return "counsel_service"
    if from_forensics:
        # Forensics fields describe the conduct by construction.
        return "exfil_context"
    if _looks_like_service_list(window):
        return "counsel_service"
    if hit_at is None:
        hit_at = len(window) // 2
    d_counsel = _nearest(_COUNSEL_RE, window, hit_at)
    d_exfil = _nearest(_EXFIL_RE, window, hit_at)
    if d_exfil is not None and d_counsel is None:
        return "exfil_context"
    if d_counsel is not None and d_exfil is None:
        return "counsel_service"
    if d_exfil is not None and d_counsel is not None:
        # Both vocabularies in the window — a conduct sentence and a signature
        # block often sit side by side in a filing. The vocabulary CLOSER to
        # the address wins; a tie reads as service plumbing (conservative).
        return "exfil_context" if d_exfil < d_counsel else "counsel_service"
    return "mention"


def automation_flags(window: str) -> list[str]:
    return [name for name, rx in _AUTOMATION_FLAGS if rx.search(window)]


def _texts(row: dict):
    """Yield (source, is_forensics, text) for every scannable string field.

    Selected projection only — enrichment_history is deliberately absent.
    """
    for key in ("title", "summary", "ai_summary", "clean_text"):
        if isinstance(row.get(key), str):
            yield key, False, row[key]
    fx = row.get("forensics") or {}
    for m in fx.get("methods") or []:
        for key in ("action", "target_data", "evidence_quote", "quantity"):
            if isinstance(m.get(key), str):
                yield f"forensics.methods.{key}", True, m[key]
        for ob in m.get("observables") or []:
            for key in ("description", "artifact"):
                if isinstance(ob.get(key), str):
                    yield f"forensics.observables.{key}", True, ob[key]
    for key in ("actor_profile", "detection", "outcome"):
        if isinstance(fx.get(key), str):
            yield f"forensics.{key}", True, fx[key]
    for lst in ("timeline", "hunt_terms", "exfil_channels", "motive_signals"):
        for item in fx.get(lst) or []:
            if isinstance(item, str):
                yield f"forensics.{lst}", True, item
    # forensics.hunt_queries is deliberately NOT scanned: pre-v3 rows carry
    # machine-generated query text (SQL LIKE patterns), not case evidence —
    # v3 removed the field from the write path (docs/schema-freeze-v3.md).
    for tm in fx.get("tool_mentions") or []:
        if isinstance(tm.get("evidence"), str):
            yield "forensics.tool_mentions.evidence", True, tm["evidence"]
    cr = row.get("case_record") or {}
    for key in ("actor_role", "access_vector", "detection_trigger", "outcome"):
        if isinstance(cr.get(key), str):
            yield f"case_record.{key}", True, cr[key]
    for lst in ("methods", "exfil_channels"):
        for item in cr.get(lst) or []:
            if isinstance(item, str):
                yield f"case_record.{lst}", True, item


_CLASS_RANK = {"exfil_context": 2, "counsel_service": 1, "mention": 0}


def scan(rows, *, context_chars: int = 160):
    """Scan CURRENT rows. Callers feed the raw JSONL through
    ``collapse_rows_by_link`` (last line wins — the store is append-only
    mid-cycle) so an updated row's latest generation is the one scanned. The
    pre-2026-09-04 version deduped first-wins here and scanned the STALE copy.
    """
    stats = Counter()
    # (link, address) -> best hit dict
    pairs: dict[tuple[str, str], dict] = {}
    for row in rows:
        stats["rows"] += 1
        link = row.get("link") or f"row-{stats['rows']}"
        stats["cases"] += 1
        if len(row.get("clean_text") or "") >= 1500:
            stats["full_body_filings"] += 1
        case_hit = False
        for source, from_fx, text in _texts(row):
            for m in EMAIL_RE.finditer(text):
                cleaned = _clean(m.group(0))
                if cleaned is None:
                    continue
                local, domain = cleaned
                stats["hits"] += 1
                case_hit = True
                w_start = max(0, m.start() - context_chars)
                window = text[w_start : m.end() + context_chars]
                cls = classify_window(window, domain, from_fx, hit_at=m.start() - w_start)
                key = (link, local.lower() + "@" + domain)
                prev = pairs.get(key)
                if prev is None or _CLASS_RANK[cls] > _CLASS_RANK[prev["class"]]:
                    pairs[key] = {
                        "link": link,
                        "title": row.get("title") or "",
                        "domain": domain,
                        "category": domain_category(domain),
                        "shape": local_shape(local),
                        "class": cls,
                        "source": source,
                        "flags": automation_flags(window),
                        "snippet": window,
                    }
                elif _CLASS_RANK[cls] == _CLASS_RANK[prev["class"]]:
                    for f in automation_flags(window):
                        if f not in prev["flags"]:
                            prev["flags"].append(f)
        if case_hit:
            stats["cases_with_address"] += 1
    return stats, list(pairs.values())


def _tally(pairs):
    by_domain = defaultdict(Counter)
    by_class = Counter()
    exfil_cat, exfil_shape, exfil_flags = Counter(), Counter(), Counter()
    for p in pairs:
        by_domain[p["domain"]][p["class"]] += 1
        by_domain[p["domain"]]["total"] += 1
        by_class[p["class"]] += 1
        if p["class"] == "exfil_context":
            exfil_cat[p["category"]] += 1
            exfil_shape[p["shape"]] += 1
            for f in p["flags"]:
                exfil_flags[f] += 1
    return by_domain, by_class, exfil_cat, exfil_shape, exfil_flags


def _snippet_fingerprint(p: dict) -> str:
    """Redacted + lowercased + whitespace-collapsed snippet text.

    Re-filed captions and repeated boilerplate collapse to one evidence entry
    (the 2026-08 run printed nine copies of one arbitration caption); tallies
    stay per-pair — only the evidence surfaces collapse.
    """
    return re.sub(r"\s+", " ", redact(p["snippet"])).strip().lower()


def _dedupe_exfil(pairs):
    """Group exfil-context pairs by (domain, snippet fingerprint).

    Returns [(representative_pair, pair_count, case_count), ...] preserving
    the webmail-first sort used by the report.
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for p in sorted(
        (p for p in pairs if p["class"] == "exfil_context"),
        key=lambda p: (p["category"] != "consumer_webmail", p["domain"], p["link"]),
    ):
        groups[(p["domain"], _snippet_fingerprint(p))].append(p)
    return [(g[0], len(g), len({p["link"] for p in g})) for g in groups.values()]


def render(stats, pairs, *, max_snippets: int = 150, top: int = 40) -> str:
    by_domain, by_class, exfil_cat, exfil_shape, exfil_flags = _tally(pairs)
    out = []
    out.append("# Email destinations in stored case documents")
    out.append("")
    out.append(
        f"Rows: **{stats['rows']}** · distinct cases: **{stats['cases']}** · "
        f"full-body filings: **{stats['full_body_filings']}** · cases with >=1 address: "
        f"**{stats['cases_with_address']}** · address hits: **{stats['hits']}** · "
        f"distinct case-address pairs: **{len(pairs)}**"
    )
    out.append("")
    out.append("> Domains only — local parts are never printed, anywhere (roles, never")
    out.append("> individuals). Context classes are keyword-derived: exfil_context =")
    out.append("> conduct language or a forensics field; counsel_service = signature/ECF")
    out.append("> vocabulary or court infrastructure; mention = neither. Treat classes as")
    out.append("> measured, not adjudicated.")
    out.append("")
    out.append("Pairs by context: " + " · ".join(f"{k} **{v}**" for k, v in by_class.most_common()))
    out.append("")
    out.append("## Domain tally (case-address pairs)")
    out.append("")
    out.append("| Domain | Total | Exfil-context | Counsel/service | Mention |")
    out.append("|---|---|---|---|---|")
    ranked = sorted(by_domain.items(), key=lambda kv: (-kv[1]["total"], kv[0]))
    for domain, c in ranked[:top]:
        out.append(
            f"| {domain} | {c['total']} | {c['exfil_context']} "
            f"| {c['counsel_service']} | {c['mention']} |"
        )
    tail = ranked[top:]
    if tail:
        out.append(f"| … {len(tail)} more domains | {sum(c['total'] for _, c in tail)} | | | |")
    out.append("")
    out.append("## Exfil-context slice")
    out.append("")
    out.append(
        "Domain category: "
        + (" · ".join(f"{k} **{v}**" for k, v in exfil_cat.most_common()) or "none")
    )
    out.append("")
    out.append(
        "Local-part shape: "
        + (" · ".join(f"{k} **{v}**" for k, v in exfil_shape.most_common()) or "none")
    )
    out.append("")
    out.append(
        "Automation flags: "
        + (" · ".join(f"{k} **{v}**" for k, v in exfil_flags.most_common()) or "none")
    )
    out.append("")
    out.append(f"## Exfil-context evidence (redacted, first {max_snippets})")
    out.append("")
    shown = 0
    shown_pairs = 0
    for p, n_pairs, n_cases in _dedupe_exfil(pairs):
        if shown >= max_snippets:
            break
        shown += 1
        shown_pairs += n_pairs
        snippet = re.sub(r"\s+", " ", p["snippet"]).strip()
        dup = f" · ×{n_pairs} ({n_cases} cases)" if n_pairs > 1 else ""
        out.append(
            f"### <redacted>@{p['domain']} · {p['category']} · shape:{p['shape']}"
            + (f" · flags:{','.join(p['flags'])}" if p["flags"] else "")
            + dup
        )
        out.append(f"{p['title']}  \n{p['link']}  \nsource: {p['source']}")
        out.append(f"> {snippet}")
        out.append("")
    remaining = by_class["exfil_context"] - shown_pairs
    if remaining > 0:
        out.append(f"_… {remaining} more exfil-context pairs not shown (raise --max-snippets)._")
    # Every line through the choke point, unconditionally: titles, snippets,
    # forensics strings — anything could quote an address.
    return "\n".join(redact(line) for line in out)


def to_json(stats, pairs) -> dict:
    by_domain, by_class, exfil_cat, exfil_shape, exfil_flags = _tally(pairs)
    return {
        "stats": dict(stats),
        "by_class": dict(by_class),
        "domains": {
            d: dict(c) for d, c in sorted(by_domain.items(), key=lambda kv: -kv[1]["total"])
        },
        "exfil_slice": {
            "category": dict(exfil_cat),
            "shape": dict(exfil_shape),
            "automation_flags": dict(exfil_flags),
        },
        "exfil_pairs": [
            {
                "domain": p["domain"],
                "category": p["category"],
                "shape": p["shape"],
                "flags": p["flags"],
                "title": redact(p["title"]),
                "link": p["link"],
                "source": p["source"],
                "snippet": redact(re.sub(r"\s+", " ", p["snippet"]).strip()),
                "duplicate_pairs": n_pairs,
                "duplicate_cases": n_cases,
            }
            for p, n_pairs, n_cases in _dedupe_exfil(pairs)
        ],
    }


def _iter_rows(path: str):
    """Current rows only: last line wins per link (core ``collapse_rows_by_link``)."""
    return _core.collapse_rows_by_link(_core.iter_jsonl_rows(path))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("corpus", help="path to processed articles.jsonl")
    ap.add_argument("--max-snippets", type=int, default=150)
    ap.add_argument("--context-chars", type=int, default=160)
    ap.add_argument("--json", dest="json_out", default=None, help="also write redacted JSON here")
    args = ap.parse_args(argv)
    stats, pairs = scan(_iter_rows(args.corpus), context_chars=args.context_chars)
    print(render(stats, pairs, max_snippets=args.max_snippets))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(to_json(stats, pairs), fh, indent=1)
        print(f"\nWrote {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
