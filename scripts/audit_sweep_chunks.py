#!/usr/bin/env python3
"""Quality audit of bulk-sweep spool chunks — run before trusting days of VM time.

Usage (inside the refresh/spark container, chunks dir mounted read-only):
    python scripts/audit_sweep_chunks.py /audit

Validates every row of every ``*.jsonl`` chunk against the lane's contracts
and prints an operator-grade report:
- parses as RawArticle; filings channel; indiacourts source id
- legal_metadata: country IN, CNR + court present, language stamped
- content: present, above the lane's min-chars floor, and NEVER contains a
  match marker (spend-gate law); summary carries the Court:/Docket: lines
  the story-key contract needs
- raw.matched_patterns non-empty; link duplicates across chunks counted
- forecast: how many rows the filings spend gate would bill today (the
  enrichment load the sweep is queueing up)
Aggregates: per-pattern and per-court histograms, language counts, text
length spread, sample titles. Exits 1 on any contract violation so the
sparky-ops op turns red.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from shared.agents.summarize import qualifies
from shared.schemas.articles import RawArticle
from shared.settings import get_settings

MARKERS = ("courtlistener query:", "indiacourts match:")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    root = Path(argv[1])
    chunks = sorted(root.glob("*.jsonl"))
    settings = get_settings()
    min_chars = settings.indiacourts_min_text_chars

    violations: list[str] = []
    rows = 0
    billable = 0
    links: Counter[str] = Counter()
    patterns: Counter[str] = Counter()
    courts: Counter[str] = Counter()
    languages: Counter[str] = Counter()
    lengths: list[int] = []
    titles: list[str] = []

    def bad(chunk: Path, line_no: int, why: str) -> None:
        violations.append(f"{chunk.name}:{line_no}: {why}")

    for chunk in chunks:
        for line_no, line in enumerate(chunk.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            rows += 1
            try:
                row = RawArticle.model_validate_json(line)
            except ValueError as exc:
                bad(chunk, line_no, f"unparseable RawArticle: {str(exc)[:120]}")
                continue
            if row.channel != "filings":
                bad(chunk, line_no, f"channel={row.channel!r} (expected filings)")
            if not (row.source_id or "").startswith("indiacourts"):
                bad(chunk, line_no, f"source_id={row.source_id!r}")
            lm = row.legal_metadata
            if lm is None:
                bad(chunk, line_no, "legal_metadata missing")
            else:
                if lm.country_code != "IN":
                    bad(chunk, line_no, f"country_code={lm.country_code!r}")
                if not lm.cnr:
                    bad(chunk, line_no, "cnr missing")
                if not lm.court_name:
                    bad(chunk, line_no, "court_name missing")
                courts[lm.court_name or "?"] += 1
                languages[lm.language or "unstamped"] += 1
                if lm.language is None:
                    bad(chunk, line_no, "language unstamped on a stored (text-bearing) row")
            content = row.content or ""
            lengths.append(len(content))
            if len(content) < min_chars:
                bad(chunk, line_no, f"content below lane floor ({len(content)} < {min_chars})")
            lowered = (content + " " + (row.summary or "")).lower()
            if any(m in lowered for m in MARKERS):
                bad(chunk, line_no, "match marker leaked into content/summary")
            summary = row.summary or ""
            if "Court:" not in summary or "Docket:" not in summary:
                bad(chunk, line_no, "summary missing Court:/Docket: story-key lines")
            matched = (row.raw or {}).get("matched_patterns") or []
            if not matched:
                bad(chunk, line_no, "raw.matched_patterns empty")
            for p in matched:
                patterns[str(p)] += 1
            if row.link:
                links[row.link] += 1
            if len(titles) < 5 and row.title:
                titles.append(row.title[:110])
            if qualifies(itm_hits=[], use_cases=[], channel="filings", text=content):
                billable += 1

    dupes = sum(c - 1 for c in links.values() if c > 1)
    print(f"chunks           : {len(chunks)}")
    print(f"rows             : {rows}")
    print(f"contract breaches: {len(violations)}")
    print(f"duplicate links  : {dupes} (harmless — store dedupes — but should stay small)")
    if lengths:
        lengths.sort()
        print(
            f"text length      : min={lengths[0]} median={lengths[len(lengths) // 2]} "
            f"max={lengths[-1]}"
        )
    print(f"gate-billable now: {billable}/{rows} (queued enrichment load)")
    print(f"languages        : {dict(languages.most_common())}")
    print("matched patterns :")
    for pattern, count in patterns.most_common(15):
        print(f"  {count:>6}  {pattern}")
    print("courts           :")
    for court, count in courts.most_common(10):
        print(f"  {count:>6}  {court}")
    if titles:
        print("sample titles    :")
        for t in titles:
            print(f"  - {t}")
    if violations:
        print("\nVIOLATIONS (first 20):")
        for v in violations[:20]:
            print(f"  {v}")
        return 1
    print("\nAll contract checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
