# Corpus baseline

A committed reference point for corpus size so growth is measurable over time.
Numbers come from the read-only [`corpus-count`](../.github/workflows/corpus-count.yml)
workflow, which downloads the live `processed/articles.jsonl` and scans it in the
runner — no Cloud Run mutation, no LLM spend.

## How to refresh

Dispatch the workflow (`Actions ▸ corpus-count ▸ Run workflow`, on `main`) and
read the `Count stale filings` step. The `already current` / `STALE` split is
measured against the enrichment model the workflow pins via `--target`; keep
that in sync with `SUMMARIZER_MODEL` in `deploy-api.yml` or the split is
meaningless (the *total* counts stay valid regardless).

## Baselines

### 2026-07-22 — pre-Voya-watchlist baseline

Taken on `main` at `61b9a12` (immediately after the CourtListener company
watchlist merged, before its first refresh had run). Target model at capture
time was `claude-sonnet-5`; prod had since moved enrichment to Haiku 4.5, so the
current/stale split below is against a stale target — the **total filings** line
is the clean baseline.

| Metric | Count |
|---|---|
| Total corpus rows | 1,672 |
| Filings (court cases) | 1,431 |
| — already enriched & current (vs Sonnet 5) | 215 |
| — stale (enriched on an older model) | 549 |
| — never enriched (backfill picks up) | 667 |

Non-filing rows (news / social / tips / publications): 1,672 − 1,431 = **241**.

The `corpus-count` target was corrected to `claude-haiku-4-5-20251001` in the
same change that added this file, so future baselines report an accurate
current/stale split.
