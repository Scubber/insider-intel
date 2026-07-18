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
  archive (≤ `COURTLISTENER_BACKFILL_MAX_DOCKETS`, default 25 attempts/run;
  never a PACER purchase) before processing.
- **Ingest summarizer (opt-in):** to enable LLM case records + summaries, set
  the env on the **job only** (the API never calls the LLM):
  `gcloud run jobs update corpus-refresh --region us-east1
  --set-env-vars SUMMARIZER_LLM_PROVIDER=anthropic
  --set-secrets ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest`.
  Spend is capped by `SUMMARIZER_MAX_ARTICLES_PER_RUN` (default 15/run ≈
  $3-4/mo on Haiku); results persist in the corpus so each article is billed
  once, and the backfill sweep converts the existing corpus gradually.
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
