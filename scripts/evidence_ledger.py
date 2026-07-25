#!/usr/bin/env python3
"""Insider Evidence Ledger — corpus-wide forensic technique aggregation.

Reads the processed-corpus JSONL and rolls every stored forensic record up
into one report (markdown to stdout, optional JSON sidecar). Four layers:

1. Technique frequency  — cases per ITM technique id, split by the strongest
   claim_status any of the case's methods carries (adjudicated/admitted vs
   alleged/reported/unclear). An adjudicated method is ground truth; an
   alleged one is a complaint's theory — never count them as the same thing.
2. Detected-by ranking  — the headline: observables aggregated across all
   cases, ranked by how many distinct cases each evidentiary artifact
   surfaced, split by basis (mechanically_implied = the defensible detection
   claim) and by the claim strength of the method it evidences.
3. Channel coverage     — observable channels (email/chat/network/endpoint/
   cloud/identity/physical/human) by distinct-case counts.
4. Coverage questions   — the honest maturity instrument: top artifacts
   restated as "can your program produce this on demand?" with the share of
   adjudicated-or-admitted cases that hinged on them. Trends ride as a
   caveated appendix (collection bias: the corpus reflects OUR query lexicon).

Pure stdlib (runs in a bare Actions runner like count_stale_filings.py); no
LLM spend; read-only.

Usage: evidence_ledger.py CORPUS.jsonl [--json ledger.json] [--top 25]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--json", dest="json_out", default=None, help="Also write a JSON sidecar")
    ap.add_argument("--top", type=int, default=25, help="Rows per ranking table")
    args = ap.parse_args(argv)

    total_rows = enriched_cases = 0
    tech_cases: dict[str, Counter] = defaultdict(Counter)  # technique -> strength -> cases
    tech_examples: dict[str, list[str]] = defaultdict(list)
    artifact_cases: dict[str, set] = defaultdict(set)  # family -> case links
    artifact_strong: dict[str, set] = defaultdict(set)  # family -> strong-case links
    artifact_mech: Counter = Counter()  # family -> mechanically_implied observable count
    artifact_channels: dict[str, Counter] = defaultdict(Counter)
    channel_cases: dict[str, set] = defaultdict(set)
    year_tech: dict[str, Counter] = defaultdict(Counter)
    strength_totals: Counter = Counter()

    with open(args.corpus, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
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

    out = []
    out.append("# Insider Evidence Ledger")
    out.append("")
    out.append(
        f"Corpus rows: **{total_rows}** · cases with extracted methods: "
        f"**{enriched_cases}** (adjudicated/admitted: {strong_total}, "
        f"alleged: {strength_totals['alleged']}, "
        f"reported/unclear: {strength_totals['reported/unclear']})"
    )
    out.append("")
    out.append("> Counting rule: a case's strength is the STRONGEST claim_status any of its")
    out.append("> methods carries. Adjudicated/admitted methods are ground truth; alleged is")
    out.append("> a complaint's theory. Never read the two columns as equivalent.")

    out.append("")
    out.append("## 1 · Technique frequency (ITM)")
    out.append("")
    out.append("| Technique | Cases | Adjud./adm. | Alleged | Exemplars |")
    out.append("|---|---|---|---|---|")
    ranked_techs = sorted(tech_cases.items(), key=lambda kv: -sum(kv[1].values()))
    for tech, counts in ranked_techs[: args.top]:
        exemplars = "; ".join(tech_examples[tech])
        out.append(
            f"| {tech} | {sum(counts.values())} | "
            f"{counts['adjudicated/admitted']} | {counts['alleged']} | {exemplars} |"
        )

    out.append("")
    out.append("## 2 · Detected by — evidence that made real cases")
    out.append("")
    out.append("Artifacts ranked by distinct cases they evidenced. `mech` counts")
    out.append("mechanically-implied observables — the defensible detection claims.")
    out.append("")
    out.append("| Evidentiary artifact | Cases | Adjud./adm. cases | mech obs | Top channels |")
    out.append("|---|---|---|---|---|")
    ranked_artifacts = sorted(artifact_cases.items(), key=lambda kv: -len(kv[1]))
    for fam, links in ranked_artifacts[: args.top]:
        chans = ", ".join(c for c, _ in artifact_channels[fam].most_common(3))
        out.append(
            f"| {fam} | {len(links)} | {len(artifact_strong[fam])} | "
            f"{artifact_mech[fam]} | {chans} |"
        )

    out.append("")
    out.append("## 3 · Channel coverage")
    out.append("")
    out.append("| Channel | Distinct cases with evidence in this channel |")
    out.append("|---|---|")
    for ch in CHANNELS:
        out.append(f"| {ch} | {len(channel_cases[ch])} |")

    out.append("")
    out.append("## 4 · Coverage questions (program self-assessment)")
    out.append("")
    out.append("For each top artifact: can your program produce this on demand, and how")
    out.append("far back? Share shown is of adjudicated/admitted cases it evidenced.")
    out.append("")
    for fam, links in ranked_artifacts[:10]:
        strong = len(artifact_strong[fam])
        share = f"{100 * strong / strong_total:.0f}%" if strong_total else "n/a"
        out.append(
            f"- **{fam}** — evidenced {len(links)} case(s); {share} of "
            f"adjudicated/admitted cases. Can you produce this artifact on demand?"
        )

    out.append("")
    out.append("## Appendix · Technique mix by filing year (CAVEAT: collection bias)")
    out.append("")
    out.append("> The corpus reflects OUR CourtListener query lexicon and sweep history —")
    out.append("> a shift below may be a change in what we collect, not in the world.")
    out.append("")
    out.append("| Year | Cases w/ techniques | Top techniques |")
    out.append("|---|---|---|")
    for year in sorted(year_tech):
        counts = year_tech[year]
        top = ", ".join(f"{t}×{n}" for t, n in counts.most_common(5))
        out.append(f"| {year} | {sum(counts.values())} | {top} |")

    report = "\n".join(out)
    print(report)

    if args.json_out:
        payload = {
            "total_rows": total_rows,
            "enriched_cases": enriched_cases,
            "strength_totals": dict(strength_totals),
            "techniques": {
                t: {"counts": dict(c), "exemplars": tech_examples[t]} for t, c in tech_cases.items()
            },
            "artifacts": {
                fam: {
                    "cases": len(links),
                    "strong_cases": len(artifact_strong[fam]),
                    "mechanical_observables": artifact_mech[fam],
                    "channels": dict(artifact_channels[fam]),
                }
                for fam, links in artifact_cases.items()
            },
            "channels": {ch: len(links) for ch, links in channel_cases.items()},
            "by_year": {y: dict(c) for y, c in year_tech.items()},
        }
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
