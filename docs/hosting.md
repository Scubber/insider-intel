# Hosting: GitHub Pages UI + live Cloud Run API

Source: **https://github.com/Scubber/insider-intel** (`main`).

Brand hub and sibling apex aliases live in **https://github.com/Scubber/thederpweb**.

## Public hostnames

| Host | Role | Target |
|------|------|--------|
| `https://intel.thederpweb.com` | Public UI (canonical) | GitHub Pages → this repo `web/` |
| `https://td3.dev` | Short alias | GitHub Pages → JS redirect to `intel.thederpweb.com` |
| `https://api.intel.thederpweb.com` | FastAPI | Cloud Run |
| `https://scubber.github.io/insider-intel/` | Same Pages site | GitHub Pages project URL |

**DNS:** `intel` CNAME → `scubber.github.io`; `td3.dev` apex A/AAAA → GitHub Pages anycast (or CNAME flattening).
**UI primary:** `web/CNAME` = `intel.thederpweb.com`.
**API:** map `api.intel.thederpweb.com` to the Cloud Run service (below).

### Fix “404 / insecure” on custom domains

GitHub only serves hostnames that appear under **Settings → Pages → Custom domains**.
DNS alone is not enough. After each primary-domain change:

1. Open **Settings → Pages** on `Scubber/insider-intel`.
2. Confirm **Primary** is `intel.thederpweb.com` (from `CNAME`).
3. **Add domain** (additional) for `td3.dev` (and `www.td3.dev` if used).
   GitHub’s REST `pages/domains` API may 404 for some accounts — use
   **Settings → Pages → Custom domains → Add a domain** in the UI if needed.
4. Wait until each shows DNS check ✓ and **TLS certificate has been issued** (can take minutes–hours). Until then browsers show “insecure” / cert errors; GitHub may also 404 the Host.
5. Enable **Enforce HTTPS**.

Do not leave only the TXT verification step complete — the domain must remain listed on that Pages settings screen.

**Cutover note:** `intel.thederpweb.com` is the Pages primary (`web/CNAME`). A custom
domain can only be attached to one repo — remove it from `Scubber/thederpweb`
before adding it here.

```text
Browser → Pages (web/) → https://api.intel.thederpweb.com  → processed JSONL
```

## Public site today (target)

- **UI:** GitHub Pages (`web/`) — Hunt-first Articles | Workbench; Matrix tab.
- **API:** Cloud Run FastAPI — same contract as local `:8000` (`/articles`, `/search`,
  `/itm`, `/extract/ttps`, `/reload`, …).
- **`web/config.js`:** points at `https://api.intel.thederpweb.com`. The site talks
  only to the live API; if it is unreachable, boot retries (cold start) then shows a
  retryable error state — there is no snapshot fallback baked into the site.

Workbench Extract TTPs **requires** the live API (CourtListener enrich + optional xAI).

## Standalone preview (separate from the site)

For sharing a working UI without a server, build a self-contained single file
that runs offline against an embedded snapshot. This is **not** part of the
deployed site — the offline responder and snapshot live under `preview/`, never `web/`:

```bash
python scripts/export_demo_snapshot.py   # refresh preview/data
python -m scripts.export_preview         # → dist/insider-intel-demo.html
```

## Production architecture (live since 2026-07-14)

```text
GitHub Pages (web/)  →  Cloud Run service insider-intel-api (us-east1)
                              │  reads /app/data (GCS FUSE mount, read-only)
                     gs://insider-intel-502413-corpus  (processed/raw/state JSONL)
                              ▲  read-write mount
Cloud Scheduler (every 6h) → Cloud Run Job corpus-refresh
                              (aggregator all → bucket → POST /reload)
```

- **Project:** `insider-intel-502413` · **Region:** `us-east1` · budget alert at $10/mo.
- **Corpus lives in GCS**, not the image. Images are corpus-free; the service
  mounts the bucket read-only at `/app/data`, the refresh job mounts it
  read-write. No more bake-and-redeploy.
- **Scheduled ingest:** Cloud Scheduler → `corpus-refresh` job → full
  `python -m apps.aggregator all` → POST `/reload` on the service.
  Run it manually anytime: `gcloud run jobs execute corpus-refresh --region us-east1`.
  Each run also backfills full court-document text from the free RECAP
  archive (≤ `COURTLISTENER_BACKFILL_MAX_DOCKETS`, default 25 attempts/run)
  and ingests one historical case window (back to
  `COURTLISTENER_HISTORY_FLOOR`) before processing. Runtime is ~8-10 min at
  the 7s CourtListener pacing; if you raise the per-run caps, also raise the
  job timeout (`gcloud run jobs update corpus-refresh --task-timeout=20m`).
- **PACER purchasing (opt-in):** to let the refresh job buy missing lead
  documents for qualifying cases via RECAP Fetch, store the PACER account
  credentials + a CourtListener token in Secret Manager and attach them to
  the **job only**:
  `gcloud run jobs update corpus-refresh --region us-east1
  --set-secrets PACER_USERNAME=PACER_USERNAME:latest,PACER_PASSWORD=PACER_PASSWORD:latest,COURTLISTENER_API_TOKEN=COURTLISTENER_API_TOKEN:latest`.
- **X/Twitter lane (opt-in):** store the developer app's consumer pair and
  attach to the job (secret names in Secret Manager may be lowercase — the
  mapping renames them):
  `gcloud run jobs update corpus-refresh --region us-east1
  --set-secrets X_CONSUMER_KEY=x_consumer_key:latest,X_CONSUMER_SECRET=x_consumer_secret:latest`.
  The pipeline mints/caches the bearer token itself; defaults
  (`X_INGEST_EVERY_HOURS=48`, `X_MAX_RESULTS=5`) fit the free tier's
  ~100 post-reads/month.
  Spend is capped at `PACER_QUARTERLY_BUDGET_CENTS` (default $27/quarter —
  under PACER's $30 fee waiver, so typical usage bills $0) and
  `PACER_PURCHASE_MAX_PER_RUN` (default 5). Estimated spend is tracked in
  `state/ingest_state.json` (`pacer_spend:YYYY-Qn`). Purchased documents
  join the public RECAP archive.
- **Hunt report — no read-time LLM:** `POST /extract/ttps` assembles each
  boarded article's stored `forensics` record into technique sections in code;
  there is no LLM call at read time, so **no LLM keys belong on the service**.
  Reports get richer as the corpus is enriched (floor fallback until then).
- **Ingest enricher (opt-in — the only LLM use):** one unified call per
  qualifying article writes the analyst note + forensic record that the hunt
  report reads. Set the provider env + key on the **job only**. Providers:
  `anthropic | openai | gemini`:
  `gcloud run jobs update corpus-refresh --region us-east1
  --update-env-vars SUMMARIZER_LLM_PROVIDER=anthropic
  --update-secrets ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest`.
  `SUMMARIZER_MODEL` overrides the model (a stronger model is worth it now that
  the call produces the full forensic record). Spend is capped by
  `SUMMARIZER_MAX_ARTICLES_PER_RUN` (library default 15/run; the **prod job is
  set to 100/run** by `deploy-api.yml`, which also sets `--task-timeout=30m` so
  the extra LLM calls don't sever the run — re-asserted on every deploy, so tune
  those two in the workflow, not by hand); each article is billed
  once (results persist), the backfill sweep converts the existing corpus
  gradually, and `SUMMARIZER_UPGRADE_LEGACY` (default on) re-bills legacy
  `case_record`-only rows once to add the forensic record. Enrichment normally
  fires only on articles with a lexical ITM/use-case hit; court filings (already
  insider-pre-filtered by the CourtListener query) additionally qualify once
  their full document body is present — `clean_text` ≥
  `SUMMARIZER_FILING_MIN_TEXT_CHARS` (default 1500; set 0 to enrich every
  filing) — so their stream cards get an analyst summary instead of the raw
  docket description.
- **Scale/cost:** `min-instances=0`, `max-instances=1`, 512Mi — rides the
  Cloud Run free tier; the instance cap doubles as a cost/abuse ceiling.
- **Endpoint guards:** `POST /extract/ttps` is rate-limited
  (`EXTRACT_RATE_PER_IP_HOUR`, `EXTRACT_RATE_GLOBAL_DAY`); `GET /export/articles`
  keeps its bearer token; secrets belong in Secret Manager, never in images.
- **Service accounts (least privilege):** `api-runtime` (bucket objectViewer),
  `ingest-job` (bucket objectAdmin), `scheduler-invoker` (job run.invoker),
  `github-deployer` (run.developer + artifactregistry.writer via OIDC only).

## Deploy the API (CI — normal path)

Merges to `main` touching `apps/`, `shared/`, `pyproject.toml`, or the
`Dockerfile` trigger [`deploy-api.yml`](../.github/workflows/deploy-api.yml):
GitHub OIDC federates into the `github-deployer` service account (Workload
Identity pool `github`, provider locked to `Scubber/insider-intel` — **no keys
stored anywhere**), builds and pushes the image, rolls the service and the
refresh job to it, then smoke-tests `/health` and `/articles`.
Manual trigger: Actions → deploy-api → Run workflow.

## Deploy the API (manual fallback)

`scripts/deploy_cloud_run.sh` still works from a `gcloud`-authed machine, but
note the corpus now comes from the bucket mount, not the image — the baked
`data/processed/articles.jsonl` layer is ignored in production.

## Refresh the public corpus

Automatic: every 6 hours via Cloud Scheduler (`corpus-refresh-schedule`).
Manual: `gcloud run jobs execute corpus-refresh --region us-east1 --wait`.
The job logs each source and finishes by POSTing `/reload`; Reddit RSS sources
429 from cloud IPs (known), everything else pulls normally.

## GitHub Pages (UI only)

Workflow: `.github/workflows/pages.yml` publishes `web/` on pushes to `main` that touch `web/`.
`web/` contains no demo/offline code — the shipped site depends on the live API.

## Local

```bash
python scripts/launch_local.py   # API :8000 + UI :5500
```

## What is static vs not

| Piece | Static? | Notes |
|-------|---------|-------|
| `web/` UI | Yes | Pages (live API only) |
| `preview/` bundle | Yes | Standalone share/demo, not deployed |
| FastAPI | No | Cloud Run container |
| Ingest / process | No | Local CLI (or future job) |

## CI/CD

1. **CI:** `.github/workflows/ci.yml` — pytest / ruff.
2. **CD (UI):** `pages.yml` on `main`.
3. **CD (API):** `scripts/deploy_cloud_run.sh` (manual / local until GH OIDC is wired).
