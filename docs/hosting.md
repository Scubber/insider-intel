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

## Deploy the API (Cloud Run)

### Prerequisites

1. GCP project with billing.
2. [`gcloud` CLI](https://cloud.google.com/sdk/docs/install) + `gcloud auth login`.
3. Docker Desktop (or equivalent) running.
4. Local corpus: `data/processed/articles.jsonl` (after `aggregator process`).

### One-shot deploy

From repo root:

```bash
export GCP_PROJECT=your-gcp-project-id
export GCP_REGION=us-east1          # optional
chmod +x scripts/deploy_cloud_run.sh
./scripts/deploy_cloud_run.sh
```

Optional secrets (create in Secret Manager first):

```bash
export CLOUD_RUN_SECRETS='XAI_API_KEY=XAI_API_KEY:latest,COURTLISTENER_API_TOKEN=COURTLISTENER_API_TOKEN:latest'
./scripts/deploy_cloud_run.sh
```

The script builds [`Dockerfile`](../Dockerfile) (bakes `articles.jsonl`), pushes to
Artifact Registry, and deploys service `insider-intel-api` with
`CORS_ORIGINS=https://intel.thederpweb.com,https://td3.dev,https://scubber.github.io`.

### Custom domain

1. Cloud Run → service → **Manage custom domains** → `api.intel.thederpweb.com`.
2. Route 53: create the record Cloud Run shows (usually CNAME / A/AAAA).
3. Wait for managed certificate.
4. `curl -sS https://api.intel.thederpweb.com/health`

### Refresh the public corpus

Ingest/process locally, then **rebuild and redeploy** the image (corpus is baked in):

```bash
python -m apps.aggregator process --force   # if needed
./scripts/deploy_cloud_run.sh
```

There is no live ingest inside the container in v1.

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
