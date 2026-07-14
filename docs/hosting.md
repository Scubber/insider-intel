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
                └─ ?demo=1 → web/demo/*.json (offline fallback only)
```

## Public site today (target)

- **UI:** GitHub Pages (`web/`) — Hunt-first Articles | Workbench; Matrix tab.
- **API:** Cloud Run FastAPI — same contract as local `:8000` (`/articles`, `/search`,
  `/itm`, `/extract/ttps`, `/reload`, …).
- **`web/config.js`:** prefers `https://api.intel.thederpweb.com`. If that host is
  unreachable, **boot auto-falls back** to the static `web/demo/` snapshot so
  Hunt / board / Extract / Copy still work.
- **Force snapshot:** `?demo=1`. **Force live (no fallback):** `?demo=0`.

Workbench Extract TTPs **requires** the live API (CourtListener enrich + optional xAI).
The old Pages-only demo-store cannot match local Workbench.

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

Static `web/demo/` snapshot is **optional fallback** (`?demo=1`), not the public default:

```bash
python scripts/export_demo_snapshot.py
# commit web/demo/ when you want an offline snapshot updated
```

## Local

```bash
python scripts/launch_local.py   # API :8000 + UI :5500
# offline UI against snapshot:
# open http://127.0.0.1:5500/?demo=1
```

## What is static vs not

| Piece | Static? | Notes |
|-------|---------|-------|
| `web/` UI | Yes | Pages |
| `web/demo/` | Yes | `?demo=1` only |
| FastAPI | No | Cloud Run container |
| Ingest / process | No | Local CLI (or future job) |

## CI/CD

1. **CI:** `.github/workflows/ci.yml` — pytest / ruff.
2. **CD (UI):** `pages.yml` on `main`.
3. **CD (API):** `scripts/deploy_cloud_run.sh` (manual / local until GH OIDC is wired).
