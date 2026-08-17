"""Select the gold set for the thinking-on/off A/B (read-only, deterministic).

Step 1 of the OPENAI_COMPAT_ENABLE_THINKING gate (docs/ab-thinking.md): pick N
already-enriched rows from a processed JSONL so the A/B re-runs cases whose
prior adjudications we can compare against. The corpus is only READ — the one
write is the manifest JSON at --out.

Stratification (each pick's manifest entry records its cell + rationale):
- eligibility: row has a forensic record AND clean_text >= --min-text-chars
  (default mirrors SUMMARIZER_FILING_MIN_TEXT_CHARS — the enrichment gate);
- coverage axes: is_insider_case verdict x method-count bucket
  (poor <= 1 < mid < 3 <= rich) x body-length bucket (short < 5k <= mid <
  20k <= long chars) x legal_posture — selection round-robins the non-empty
  cells so every axis gets spread;
- within a cell, rows carrying a STRONG BASELINE (a claude-sonnet-5
  generation in enrichment_history) are taken first, so verdict agreement
  can be scored against a stronger model where one exists.

Deterministic given the corpus: ordering inside a cell is by
sha256(seed:link) — no timestamps, no randomness beyond the seed.

Usage (from the repo root):
    python -m scripts.ab_select_goldset \
        [--processed-path data/processed/articles.jsonl] \
        [--out data/ab_eval/goldset_manifest.json] [--n 40] [--seed 42]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from shared.schemas import ProcessedArticle
from shared.schemas.forensics import EnrichmentRecord

DEFAULT_N = 40
DEFAULT_SEED = 42
# The enrichment spend gate for filings (SUMMARIZER_FILING_MIN_TEXT_CHARS
# default): below this there is no document body worth re-adjudicating.
DEFAULT_MIN_TEXT_CHARS = 1_500
# A "strong baseline" generation: prefix-matched against
# EnrichmentRecord.forensics.model (covers dated SKUs like
# claude-sonnet-5-20260115).
STRONG_BASELINE_MODEL_PREFIX = "claude-sonnet-5"
# Method-count buckets: poor <= POOR_MAX < mid < RICH_MIN <= rich.
METHOD_POOR_MAX = 1
METHOD_RICH_MIN = 3
# Body-length buckets (clean_text chars): short < SHORT_LT <= mid < LONG_GE <= long.
LENGTH_SHORT_LT = 5_000
LENGTH_LONG_GE = 20_000

MANIFEST_KIND = "ab-thinking-goldset"


def iter_processed(path: str | Path) -> list[ProcessedArticle]:
    """Read a processed JSONL without ever writing (last line per link wins).

    Deliberately not JsonlProcessedStore: the store's constructor mkdirs and
    its API invites writes — the A/B harness must be provably read-only over
    the corpus.
    """
    rows: dict[str, ProcessedArticle] = {}
    file_path = Path(path)
    if not file_path.exists():
        return []
    with file_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = ProcessedArticle.model_validate_json(line)
            except Exception:  # noqa: BLE001 — corrupt lines skip, mirroring the store
                continue
            rows[row.link] = row  # later lines win
    return list(rows.values())


def order_key(seed: int, link: str) -> str:
    """Deterministic per-seed shuffle key (no RNG state, no wall clock)."""
    return hashlib.sha256(f"{seed}:{link}".encode()).hexdigest()


def method_bucket(methods_count: int) -> str:
    if methods_count >= METHOD_RICH_MIN:
        return "rich"
    if methods_count <= METHOD_POOR_MAX:
        return "poor"
    return "mid"


def length_bucket(chars: int) -> str:
    if chars >= LENGTH_LONG_GE:
        return "long"
    if chars < LENGTH_SHORT_LT:
        return "short"
    return "mid"


def find_baseline(
    row: ProcessedArticle, *, model_prefix: str = STRONG_BASELINE_MODEL_PREFIX
) -> tuple[int, EnrichmentRecord] | None:
    """Newest enrichment_history generation from the strong-baseline model."""
    best: tuple[int, EnrichmentRecord] | None = None
    for idx, rec in enumerate(row.enrichment_history or []):
        if not rec.model.startswith(model_prefix):
            continue
        if best is None:
            best = (idx, rec)
            continue
        prev = best[1].forensics.extracted_at
        cur = rec.forensics.extracted_at
        # Newer wins; None timestamps lose; ties keep the later history index.
        if prev is None or (cur is not None and cur >= prev):
            best = (idx, rec)
    return best


def _case_entry(row: ProcessedArticle, *, baseline_prefix: str) -> dict:
    forensics = row.forensics
    assert forensics is not None  # eligibility guaranteed by caller
    chars = len(row.clean_text or "")
    baseline = find_baseline(row, model_prefix=baseline_prefix)
    entry = {
        "link": row.link,
        "title": row.title,
        "source_id": row.source_id,
        "channel": row.channel,
        "clean_text_chars": chars,
        "is_insider_case": bool(forensics.is_insider_case),
        "methods_count": len(forensics.methods or []),
        "method_bucket": method_bucket(len(forensics.methods or [])),
        "length_bucket": length_bucket(chars),
        "legal_posture": forensics.legal_posture or "unknown",
        "current_model": (forensics.model or "").strip(),
        "baseline": None,
    }
    if baseline is not None:
        idx, rec = baseline
        entry["baseline"] = {
            "model": rec.model,
            "history_index": idx,
            "is_insider_case": bool(rec.forensics.is_insider_case),
            "confidence": float(rec.forensics.confidence or 0.0),
            "extracted_at": (
                rec.forensics.extracted_at.isoformat() if rec.forensics.extracted_at else None
            ),
        }
    return entry


def select_goldset(
    rows: list[ProcessedArticle],
    *,
    n: int = DEFAULT_N,
    seed: int = DEFAULT_SEED,
    min_text_chars: int = DEFAULT_MIN_TEXT_CHARS,
    baseline_prefix: str = STRONG_BASELINE_MODEL_PREFIX,
    processed_path: str = "",
) -> dict:
    """Build the manifest dict. Pure function of its inputs (deterministic)."""
    eligible = [
        row
        for row in rows
        if row.forensics is not None and len(row.clean_text or "") >= min_text_chars
    ]
    entries = [_case_entry(row, baseline_prefix=baseline_prefix) for row in eligible]

    # Cell = one point on every stratification axis. Inside a cell,
    # baseline-backed rows first, then the seeded hash order.
    cells: dict[tuple, list[dict]] = {}
    for entry in entries:
        key = (
            entry["is_insider_case"],
            entry["method_bucket"],
            entry["length_bucket"],
            entry["legal_posture"],
        )
        cells.setdefault(key, []).append(entry)
    for members in cells.values():
        members.sort(key=lambda e: (e["baseline"] is None, order_key(seed, e["link"])))

    # Picks strictly alternate the verdict axis, each verdict rotating through
    # its own cell list. A plain sorted-cells round-robin starved
    # verdict=True out of the ENTIRE gold set on the real corpus: str-sorting
    # puts every (False, ...) cell first, and with >= n non-empty False cells
    # round one fills n before reaching a single True cell. Alternation
    # guarantees an even verdict split whenever supply allows, degrading to
    # whatever remains when one side runs dry.
    #
    # Within a verdict, cells are ordered by seeded hash, NOT str sort: str
    # order groups cells by the next axis value ('mid' < 'poor' < 'rich'), so
    # when the per-verdict budget is smaller than the cell count the last
    # axis value gets starved the same way (the real corpus drew 0
    # method-rich picks). Hash order decorrelates iteration from every axis,
    # spreading truncation evenly — and stays deterministic per seed.
    keys_by_verdict = {
        True: sorted((k for k in cells if k[0]), key=lambda k: order_key(seed, str(k))),
        False: sorted((k for k in cells if not k[0]), key=lambda k: order_key(seed, str(k))),
    }
    cursors = {True: 0, False: 0}

    def pop_next(verdict: bool) -> tuple | None:
        keys = keys_by_verdict[verdict]
        for j in range(len(keys)):
            key = keys[(cursors[verdict] + j) % len(keys)]
            if cells[key]:
                cursors[verdict] = (cursors[verdict] + j + 1) % len(keys)
                return key
        return None

    picked: list[dict] = []
    turn = True  # insider-true first: the scarcer, higher-stakes axis
    while len(picked) < n and any(cells.values()):
        key = pop_next(turn) or pop_next(not turn)
        if key is None:
            break
        entry = cells[key].pop(0)
        verdict, mbucket, lbucket, posture = key
        entry["rationale"] = (
            f"pick {len(picked) + 1} for cell verdict={verdict} "
            f"methods={mbucket}({entry['methods_count']}) "
            f"length={lbucket}({entry['clean_text_chars']}ch) posture={posture}; "
            + (
                f"strong baseline {entry['baseline']['model']} present"
                if entry["baseline"]
                else "no strong baseline"
            )
        )
        picked.append(entry)
        turn = not turn

    strata = {
        "verdict_true": sum(1 for e in picked if e["is_insider_case"]),
        "verdict_false": sum(1 for e in picked if not e["is_insider_case"]),
        "with_baseline": sum(1 for e in picked if e["baseline"]),
        "method_buckets": _counts(picked, "method_bucket"),
        "length_buckets": _counts(picked, "length_bucket"),
        "legal_postures": _counts(picked, "legal_posture"),
    }
    return {
        "kind": MANIFEST_KIND,
        "seed": seed,
        "n_requested": n,
        "n_selected": len(picked),
        "min_text_chars": min_text_chars,
        "baseline_model_prefix": baseline_prefix,
        "processed_path": processed_path,
        "corpus_rows": len(rows),
        "eligible_rows": len(eligible),
        "strata_counts": strata,
        "cases": picked,
    }


def _counts(entries: list[dict], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for entry in entries:
        out[entry[field]] = out.get(entry[field], 0) + 1
    return dict(sorted(out.items()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--processed-path", default="data/processed/articles.jsonl")
    ap.add_argument("--out", default="data/ab_eval/goldset_manifest.json")
    ap.add_argument("--n", type=int, default=DEFAULT_N)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--min-text-chars", type=int, default=DEFAULT_MIN_TEXT_CHARS)
    ap.add_argument("--baseline-model-prefix", default=STRONG_BASELINE_MODEL_PREFIX)
    args = ap.parse_args()

    rows = iter_processed(args.processed_path)
    manifest = select_goldset(
        rows,
        n=args.n,
        seed=args.seed,
        min_text_chars=args.min_text_chars,
        baseline_prefix=args.baseline_model_prefix,
        processed_path=args.processed_path,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        f"gold set: {manifest['n_selected']}/{manifest['n_requested']} cases "
        f"from {manifest['eligible_rows']} eligible ({manifest['corpus_rows']} corpus rows)"
    )
    print(f"strata: {json.dumps(manifest['strata_counts'])}")
    print(f"manifest written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
