"""Vendor case-mention scanner: which vendors the stored case documents NAME.

Operator decision (2026-08-17): the TOOLING page's vendors are named and
RANKED, and the defensible basis is documented case mentions — the number of
distinct cases whose stored court text actually names the product. Presence
in the record, never an effectiveness score, and NEVER an input to the
category ranking math in ``apps.search.tooling.rank_tool_categories``
(tests pin byte-identity with the aliases file stripped).

Mechanics (zero LLM, deterministic):

- The alias map is checked in at ``shared/data/vendor_aliases.json`` — one
  entry per (vendor, category) pair in tooling_map.json's ``examples``, each
  with distinctive alias strings (safety rules documented in the file; a
  vendor with no safe alias carries an empty list + ``no_safe_alias`` note
  and can never miscount).
- Matching is ONE compiled regex alternation pass per document:
  case-insensitive, word-boundary (the ``(?<![a-z0-9])…(?![a-z0-9])``
  lookarounds the index's alias matcher uses), multi-word aliases tolerant
  of any whitespace run. Alternatives are ordered longest-first, so a more
  specific product name shadows a shorter prefix at the same position
  ("Splunk UBA" credits the UBA entry there; an unqualified "Splunk"
  elsewhere in the same text still credits the SIEM entry).
- Counts are DISTINCT case links, split into verdict-true (rows whose stored
  forensics adjudicated ``is_insider_case is True`` — the same gate the
  evidence ledger applies) and total mentions (any indexed document).
- Case RECEIPTS: alongside the counts, each vendor carries the actual case
  references (link, title, verdict flag, published date) for the NAMED TOOLS
  directory's vendor sheet — capped at the ``VENDOR_CASE_REFS_CAP`` most
  recent by published date (undated rows sort last), with ``more_cases``
  carrying the count beyond the cap. Receipts are display-only decoration
  under the same never-a-ranking-input contract as the counts.
- Cache contract: computed lazily ONCE per index generation.
  ``mentions_for_index`` keys a WeakKeyDictionary on the ArticleSearchIndex
  object itself — the same lifecycle as the service's index singleton — so
  /reload's index swap invalidates by construction, per-request /tooling
  calls never rescan the corpus, and a dropped index takes its scan with it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from weakref import WeakKeyDictionary

VENDOR_ALIASES_PATH = (
    Path(__file__).resolve().parents[2] / "shared" / "data" / "vendor_aliases.json"
)

# Vendor-sheet receipts cap: each vendor's payload carries at most this many
# case references (the most recent by published date); ``more_cases`` reports
# how many further naming cases exist beyond the cap. Keeps the /tooling
# payload bounded (164 vendor entries × 25 refs worst case) while the sheet
# stays a real receipts list, not a bare count.
VENDOR_CASE_REFS_CAP = 25


@lru_cache(maxsize=1)
def load_vendor_aliases(path: str | None = None) -> dict:
    """The checked-in vendor → alias map (authored, sweep-stable)."""
    with open(path or VENDOR_ALIASES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def compile_alias_matcher(
    vendors: list[dict],
) -> tuple[re.Pattern | None, dict[str, list[tuple[str, str]]]]:
    """One alternation over every alias, longest alias first.

    Returns ``(pattern, homes)`` where ``homes`` maps each normalized alias
    to the [(category, vendor name), ...] entries it credits — one alias may
    legitimately credit two homes (Netskope lives in both dlp and casb).
    ``pattern`` is None when no vendor has any alias.

    The pattern is compiled over the lowercase-normalized aliases and expects
    LOWERCASED text (``scan_vendor_mentions`` lowers each document once) —
    measured ~2.5x faster over a 7k-document corpus than an IGNORECASE
    alternation, and byte-identical in what it matches.
    """
    homes: dict[str, list[tuple[str, str]]] = {}
    for vendor in vendors:
        key = (str(vendor.get("category") or ""), str(vendor.get("name") or ""))
        for alias in vendor.get("aliases") or []:
            normalized = _normalize(str(alias))
            if not normalized:
                continue
            entries = homes.setdefault(normalized, [])
            if key not in entries:
                entries.append(key)
    if not homes:
        return None, {}
    # Longest-first so specific product names win the alternation at a
    # position ("proofpoint itm" before "proofpoint").
    ordered = sorted(homes, key=lambda a: (-len(a), a))
    body = "|".join(r"\s+".join(re.escape(word) for word in alias.split()) for alias in ordered)
    pattern = re.compile(rf"(?<![a-z0-9])(?:{body})(?![a-z0-9])")
    return pattern, homes


def scan_vendor_mentions(rows: Iterable[dict], vendors: list[dict]) -> dict:
    """Pure scanner core (deterministic; unit-tested with synthetic rows).

    ``rows`` — {"link", "clean_text", "verdict_true"} dicts (optionally
    "title" and "published", an ISO date string, for the case receipts);
    ``verdict_true`` mirrors the ledger's gate (stored forensics say
    ``is_insider_case is True`` — False and missing both fail it).
    ``vendors`` — vendor_aliases.json ``vendors`` entries.

    Returns ``{"scanned_articles": n, "mentions": {(category, name):
    {"total": set(links), "verdict_true": set(links)}}, "cases": {link:
    {"title", "verdict_true", "published"}}}`` — distinct case links, never
    occurrence counts; ``cases`` holds display metadata for rows that
    matched at least one vendor (the vendor-sheet receipts).
    """
    pattern, homes = compile_alias_matcher(vendors)
    mentions: dict[tuple[str, str], dict[str, set[str]]] = {
        (str(v.get("category") or ""), str(v.get("name") or "")): {
            "total": set(),
            "verdict_true": set(),
        }
        for v in vendors
    }
    cases: dict[str, dict] = {}
    scanned = 0
    for row in rows:
        scanned += 1
        text = str(row.get("clean_text") or "")
        if not text or pattern is None:
            continue
        link = str(row.get("link") or f"row-{scanned}")
        verdict_true = row.get("verdict_true") is True
        hit: set[tuple[str, str]] = set()
        # One lower() per document, matched by the lowercase-compiled pattern
        # (cheaper than an IGNORECASE alternation over the whole corpus).
        for match in pattern.finditer(text.lower()):
            hit.update(homes.get(_normalize(match.group(0)), ()))
        if hit:
            cases[link] = {
                "title": str(row.get("title") or "") or link,
                "verdict_true": verdict_true,
                "published": row.get("published") or None,
            }
        for key in hit:
            slot = mentions[key]
            slot["total"].add(link)
            if verdict_true:
                slot["verdict_true"].add(link)
    return {"scanned_articles": scanned, "mentions": mentions, "cases": cases}


# One scan per index generation: keyed weakly on the index object (the
# service swaps in a NEW ArticleSearchIndex on /reload, so the swap itself
# is the invalidation — same lifecycle the index singleton follows).
_index_scans: WeakKeyDictionary = WeakKeyDictionary()


def mentions_for_index(index) -> dict:
    """Cached vendor-mention scan for one loaded index generation."""
    scan = _index_scans.get(index)
    if scan is None:
        vendors = load_vendor_aliases().get("vendors") or []
        rows = (
            {
                "link": article.link,
                "title": article.title,
                "clean_text": article.clean_text,
                "published": (article.published.isoformat() if article.published else None),
                "verdict_true": (
                    article.forensics is not None and article.forensics.is_insider_case is True
                ),
            }
            for article in index.articles
        )
        scan = scan_vendor_mentions(rows, vendors)
        _index_scans[index] = scan
    return scan


def vendor_case_refs(links: set[str] | frozenset[str], case_meta: dict) -> tuple[list[dict], int]:
    """Capped, ordered case receipts for one vendor.

    Orders the vendor's distinct naming cases most-recent-published first
    (undated cases last; equal dates tie-break by link ascending, so the
    order is deterministic), keeps the head ``VENDOR_CASE_REFS_CAP`` and
    returns ``(refs, more)`` where ``more`` counts the cases beyond the cap.
    """
    refs = [
        {
            "link": link,
            "title": str(meta.get("title") or "") or link,
            "verdict_true": meta.get("verdict_true") is True,
            "published": meta.get("published") or None,
        }
        for link in links
        for meta in (case_meta.get(link) or {"title": link},)
    ]
    refs.sort(key=lambda r: r["link"])  # deterministic tie-break (stable sort below)
    refs.sort(key=lambda r: str(r["published"] or ""), reverse=True)  # newest first, undated last
    return refs[:VENDOR_CASE_REFS_CAP], max(0, len(refs) - VENDOR_CASE_REFS_CAP)


def attach_vendor_mentions(category_rows: list[dict], scan: dict) -> None:
    """Decorate ranked /tooling category rows with mention-ranked vendors.

    Adds a ``vendors`` list to each row — every ``examples`` vendor with its
    ``mentions_cases`` counts, ordered verdict-true mentions desc, then total
    mentions desc, then name, each carrying its capped case receipts
    (``cases`` + ``more_cases``, see ``vendor_case_refs``) — and touches
    NOTHING else: the category ranking fields stay byte-identical whether
    the aliases file exists or not (test-pinned).
    """
    mentions = scan.get("mentions") or {}
    case_meta = scan.get("cases") or {}
    for row in category_rows:
        category_id = str(row.get("id") or "")
        vendors = []
        for name in row.get("examples") or []:
            slot = mentions.get((category_id, str(name))) or {}
            refs, more = vendor_case_refs(slot.get("total") or set(), case_meta)
            vendors.append(
                {
                    "name": str(name),
                    "mentions_cases": {
                        "verdict_true": len(slot.get("verdict_true") or ()),
                        "total": len(slot.get("total") or ()),
                    },
                    "cases": refs,
                    "more_cases": more,
                }
            )
        vendors.sort(
            key=lambda v: (
                -v["mentions_cases"]["verdict_true"],
                -v["mentions_cases"]["total"],
                v["name"].lower(),
            )
        )
        row["vendors"] = vendors
