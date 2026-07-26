"""Count billed enrichments the LLM itself adjudicated as NOT an insider case.

Stdlib-only (runs on a bare Actions runner against a downloaded corpus copy).

Usage: python3 scripts/count_non_insider_enrichments.py corpus.jsonl
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter

NOT_INSIDER_RE = re.compile(r"not an insider", re.IGNORECASE)


def main(path: str) -> int:
    total = 0
    enriched = 0
    by_verdict: Counter[str] = Counter()
    non_insider_by_channel: Counter[str] = Counter()
    non_insider_by_source: Counter[str] = Counter()
    phrase_hits = 0
    non_insider_no_methods = 0
    examples: list[str] = []

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            forensics = row.get("forensics")
            if not isinstance(forensics, dict):
                continue
            enriched += 1
            insider = bool(forensics.get("is_insider_case"))
            by_verdict["insider" if insider else "non_insider"] += 1
            summary = str(row.get("ai_summary") or "")
            if NOT_INSIDER_RE.search(summary):
                phrase_hits += 1
            if not insider:
                non_insider_by_channel[str(row.get("channel") or "?")] += 1
                non_insider_by_source[str(row.get("source_id") or "?")] += 1
                if not forensics.get("methods"):
                    non_insider_no_methods += 1
                if len(examples) < 8:
                    examples.append(str(row.get("title") or "")[:90])

    print(f"total rows: {total}")
    print(f"enriched (forensics present): {enriched}")
    print(f"verdicts: {dict(by_verdict)}")
    print(f"ai_summary contains 'not an insider': {phrase_hits}")
    print(f"non-insider with zero methods: {non_insider_no_methods}")
    print("non-insider by channel:", dict(non_insider_by_channel.most_common()))
    print("non-insider top sources:")
    for src, n in non_insider_by_source.most_common(15):
        print(f"  {n:4d}  {src}")
    print("example non-insider titles:")
    for t in examples:
        print(f"  - {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
