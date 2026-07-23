# HANDOFF — current state for the next session / IDE / LLM

**Read this to pick up where the last session left off.** It is the live
operational state; [`../CLAUDE.md`](../CLAUDE.md) is the architecture/operating
manual, [`hosting.md`](hosting.md) the production detail, and the merged PRs
(linked below) are the diff-level changelog.

**Last updated:** 2026-07-22 · **Repo:** `Scubber/insider-intel` · **Prod:**
API on Cloud Run (`insider-intel-api`), UI on GitHub Pages
(`intel.thederpweb.com`), corpus in GCS, corpus-refresh job every 6h.

---

## Live production state

| Area | State |
|---|---|
| **CourtListener API** | **Paid Tier-2** token (15/min · 150/hr · 600/day), mapped to the job as secret `COURTLISTENER_API_TOKEN`. Tier is account-level, so the same token now runs at Tier-2. |
| **Ingest pacing/caps** | `COURTLISTENER_REQUEST_DELAY_SECONDS=5`, `COURTLISTENER_HISTORY_QUERIES_PER_WINDOW=0` (full-query rotation each run = history **drain mode**), `COURTLISTENER_BACKFILL_MAX_DOCKETS=50`. Budget ≈ 110 req/run × 4 runs ≈ 440/day (< 600). Set in `deploy-api.yml`. |
| **History sweep** | Now rotates the **full** `DEFAULT_QUERIES` set (was a hand-picked 4), so social-engineering / sim-swap / device-identifier (Scattered-Spider class) cases finally get swept for old filings. Cursor holds until a window's rotation completes. |
| **Enrichment** | **ON.** Haiku 4.5 (`SUMMARIZER_MODEL=claude-haiku-4-5-20251001`), `SUMMARIZER_MAX_ARTICLES_PER_RUN=100` + discoverer 15/run. Bills LLM every run. |
| **PACER purchasing** | **Creds mapped but PARKED** — `PACER_PURCHASE_MAX_PER_RUN=0`. The account is **Under Review** (search privileges inactive). Do NOT un-park until the account clears, or attempts fail and the est-spend counter can be consumed. |
| **Corpus size** | ≈ **1,672 rows / 1,431 filings**; only **~295 filings carry a document body** (`clean_text ≥ 1500`). **~79% are metadata stubs** whose documents are PACER-only (not in the free RECAP archive). |
| **Company watchlist** | `COURTLISTENER_COMPANY_WATCHLIST=Voya, Voya India` — each expands to a scoped insider query + a bare catch-all. |

**The central constraint:** the operator wants full court-filing *text* for
off-site LLM enrichment, but ~79% of flagged filings are stubs. The free-archive
backfill (now at 50/run, Tier-2) fills what's free; the rest needs PACER, which
is parked pending account review.

---

## This session's changes (PRs on `main`)

| PR | What / why |
|---|---|
| [#92](https://github.com/Scubber/insider-intel/pull/92) | Enrich on Haiku 4.5 to fill the backlog at ~1/3 Sonnet cost |
| [#93](https://github.com/Scubber/insider-intel/pull/93) | CourtListener **company watchlist** (Voya) → scoped + catch-all queries |
| [#94](https://github.com/Scubber/insider-intel/pull/94) | Corpus **baseline** doc + point `corpus-count` at the Haiku target |
| [#95](https://github.com/Scubber/insider-intel/pull/95) | Doc: how the CourtListener ingest finds court documents |
| [#96](https://github.com/Scubber/insider-intel/pull/96) | `corpus-count`: report **body-text coverage** (full vs metadata stub) |
| [#97](https://github.com/Scubber/insider-intel/pull/97) | Read-only `corpus-sample` workflow to inspect extracted observables |
| [#98](https://github.com/Scubber/insider-intel/pull/98) | `corpus-sample`: body-regex filter to probe artifact coverage |
| [#99](https://github.com/Scubber/insider-intel/pull/99) | `courtlistener-worklist`: topic fetch worklist (body vs stub) |
| [#100](https://github.com/Scubber/insider-intel/pull/100) | **History sweep = full query set** via per-window rotation |
| [#101](https://github.com/Scubber/insider-intel/pull/101) | **Tune ingest for Tier-2** token (pacing/caps) |
| [#102](https://github.com/Scubber/insider-intel/pull/102) | **Park PACER** purchasing until the account clears review |
| [#103](https://github.com/Scubber/insider-intel/pull/103) | **`export-llm`** workflow: pull LLM-ready docs for off-site enrichment |

---

## Operational knobs & where they live

- **Models / caps / pacing / PACER cap** → `.github/workflows/deploy-api.yml`
  `--update-env-vars` block. **GitOps: edit + merge**, not `gcloud`. The deploy
  re-stamps the job env on every merge to `main` (merge preserves secret
  mappings).
- **Secrets** (CL token, PACER `pacerinsider`/password, LLM keys) → **Secret
  Manager**, mapped to the job with `--set-secrets` **once** (persists). Never in
  the repo.
- **Read-only diagnostic + export workflows** (Actions → Run workflow):
  - `corpus-count` — totals + body-coverage + stale-vs-current split.
  - `corpus-sample` — sample enriched filings' methods/observables (`match` regex,
    `max_cases`).
  - `courtlistener-worklist` — for a `topic` regex, which flagged filings have a
    body vs are stubs.
  - `export-llm` — writes `gs://insider-intel-502413-corpus/export/insider-<channel>-llm.ndjson`
    (raw `clean_text` + metadata, body-bearing rows only). Inputs: `channel`,
    `min_body`, `include_ours`. Pull with `gcloud storage cp <path> .`.
  - `reenrich-drain`, `corpus-recover`, `corpus-status` — pre-existing job admin.

---

## Open threads (decided but NOT yet built)

1. **In-repo settings + lexicon config** — chosen direction: a checked-in
   `config/app_config.json` (edited via merge, no GCP) holding the search lexicon
   (CourtListener queries / opinion queries / watchlist / feeds) with fallback to
   `DEFAULT_QUERIES`/`DEFAULT_FEEDS`, plus an optional non-secret `settings`
   override map (file > env). **Secrets stay in Secret Manager** (code allowlist).
   Lexicon is the core; settings-override optional. Reuse the fail-soft-read idiom
   from `apps/aggregator/social_subscriptions.py`; seam is
   `courtlistener.py::parse_queries(defaults=)` + `courtlistener_pipeline.py::queries_for()`
   / `history_rotation_queries()`. Explicitly NOT a GCS bucket store, API write
   endpoint, or admin web page.
2. **Collect-only vs enrich** — if enrichment is done off-site, set
   `SUMMARIZER_MAX_ARTICLES_PER_RUN=0` (+ discoverer 0) to stop paying Haiku;
   trade-off is the live app stops enriching new cases.
3. **PACER activation** — when the account clears review, flip
   `PACER_PURCHASE_MAX_PER_RUN` 0→5 in `deploy-api.yml` + merge. Only then do
   un-free affidavits (the intrusion-case bodies) start landing. Budget hard-cap
   `PACER_QUARTERLY_BUDGET_CENTS=2700` ($27, under the $30 fee waiver).
4. **Discovery lanes** — (a) ingest the FLP tech-cases-bot feed
   (`mastodon.social/@techcases.rss`) → extract CourtListener docket links (prod
   egress can reach mastodon; the build sandbox cannot); (b) an ITM-derived query
   generator to replace hand-authored `DEFAULT_QUERIES` (systematic coverage).
5. **Off-site LLM enrichment is the operator's end goal.** `export-llm` is the
   delivery mechanism; it grows as the backfill pulls more bodies.

---

## Environment note for the assistant

The build/agent sandbox **cannot reach GCP** (network policy blocks all cloud
egress — same on any provider, so a platform move does not help). Do infra
changes through **GitOps (edit `deploy-api.yml` + merge)** or **read-only OIDC
workflows** dispatched from the Actions tab. Truly manual, one-time steps that
stay with the operator: creating Secret Manager secret *values* and account
signups (e.g. PACER). Everything else can be a committed script/workflow.

---

## Next steps

1. Watch free-body coverage climb under Tier-2: re-run `corpus-count` /
   `courtlistener-worklist` (topic = `scattered spider|sim.?swap|device identifier`)
   in a day or two; confirm the history-rotation is pulling the intrusion cases.
2. Decide **collect-only vs keep enrichment on** (thread #2).
3. When PACER clears review, un-park purchasing (thread #3).
4. Build the **in-repo lexicon/settings config** (thread #1) if routine, no-merge
   tuning is wanted — or keep editing `deploy-api.yml`.
5. Re-run `export-llm` whenever a fresh LLM-ready pull is needed.

## Verification (all local — no GCP)

```bash
make test lint fmt          # same targets CI runs; green local == green CI
# or: ruff check apps shared tests && pytest -q
```
Dispatch any workflow from **Actions → <name> → Run workflow**; read its log for
results (the `export-llm` log prints the exact `gcloud storage cp` pull command).
