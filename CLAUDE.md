# CLAUDE.md — agent operating manual

Insider-risk OSINT aggregator. **This repo is in production**: UI on GitHub
Pages, API on Cloud Run, corpus in GCS, self-refreshing every 6h, CD on merge
to `main`. Read this before changing anything; deeper docs in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) (dev env) and
[docs/hosting.md](docs/hosting.md) (production).

## Map

```
intel.thederpweb.com (Pages, web/) ──► api.intel.thederpweb.com (Cloud Run: insider-intel-api)
                                          │ GCS FUSE mount at /app/data
                       gs://insider-intel-502413-corpus  (processed/ raw/ state/ config/)
                                          ▲ read-write
Cloud Scheduler (0 */6 ET) ──► Cloud Run Job corpus-refresh ──► POST /reload
```

GCP project `insider-intel-502413`, region `us-east1`, $10/mo budget alert.

## Everyday commands

```bash
make up / down / shell / logs      # local stack: API :8000, UI :5500, Postgres :5432
make test / lint / fmt / precommit # same commands CI runs — green local == green CI
gcloud run jobs execute corpus-refresh --region us-east1 --wait   # force a corpus refresh
gcloud logging read 'resource.labels.job_name=corpus-refresh' --freshness=6h \
  --format='value(textPayload)' | grep -E '\[OK\]|\[FAIL\]|reloaded'
```

Deploys: **merge to `main`** → `ci.yml` + `deploy-api.yml` (keyless OIDC via
Workload Identity pool `github`; no stored credentials) + `pages.yml` for
`web/**`. There is no laptop deploy step; `scripts/deploy_cloud_run.sh` is a
legacy fallback.

## Invariants — do not break

- **Corpus lives in the bucket, never in images.** The Dockerfile's final
  stage must stay the Cloud Run `runtime` stage (plain `docker build .`
  produces it; the deploy workflow and legacy script rely on that).
- **DB/config flows only through `shared/settings.py`** (pydantic-settings,
  env aliases). Never scatter connection strings or `os.environ` reads.
- **The API's bucket access is read-only except the `config/` prefix**
  (IAM condition for `api-runtime`). Anything else the API must write is a
  design change, not a mount flag.
- **`POST /extract/ttps` spends LLM credits** — it is rate-limited
  (`apps/search/ratelimit.py`, env-tunable). Don't remove the limiter; the
  service also runs `max-instances=1` as a cost/abuse ceiling.
- **Match-signal text goes in `RawArticle.content`, never `summary`** —
  summaries render in the UI; `content` is scored but hidden (see the
  CourtListener query-tag fix).
- Secrets: Secret Manager / env only. `detect-secrets` hook + baseline are
  enforced via pre-commit (`make precommit`).
- Actions in workflows are **SHA-pinned**; keep it that way.

## Verification habits

- `.mcp.json` provides **Playwright MCP** (official Docker image,
  `--network=host`) — use it to drive/screenshot local (:5500/:8000) or prod
  UI. Physical-click testing catches what curl can't (see gotchas).
- `deploy-api.yml` smoke-tests `/health`, `/articles`, `/social/catalog`, and
  a subscription write round-trip after every deploy. Extend it when adding
  endpoints with side effects.
- After lexicon/taxonomy/feed changes: `process --force`, then `POST /reload`
  (locally automatic on next request; prod via the refresh job or curl).

## Hard-won gotchas

- **UI silently falls back to the static `web/demo/` snapshot** whenever the
  API boot check fails (including Cloud Run cold starts). "Snapshot <date>"
  in the status badge means the live API wasn't reached — not that the deploy
  failed. Badge shows "Updated N min ago" from `/health.last_indexed_at`.
- **Cloud Run domain-mapping certs propagate slowly**: the console can say
  provisioned while edges still fail TLS for a while. Verify with your own
  repeated curls before cutting anything over.
- **Header stacking:** `header.top` has a transform (stacking context) and
  `z-index: 10` so expanded header content isn't covered by the pane grid,
  which otherwise silently eats clicks. The panes have `overflow: hidden` —
  tooltips inside them must open inward (`data-tip-pos` variants).
- **Reddit 429s cloud IPs** on its public JSON endpoints; Reddit ingest fails
  from GCP until OAuth creds exist. X ingest needs `X_BEARER_TOKEN`.
- **GHA runners run containers with a different uid** than the checkout owner:
  tests must write only under `tmp_path` / patched settings paths (see
  `tests/test_extract_rate_limit.py`).
- Ubuntu's bare `docker.io` lacks BuildKit; the Dockerfile uses heredocs, so
  `apt install docker-buildx` is required (compose builds are fine).

## Branch/PR conventions

Work on branches; `main` deploys. Commit messages explain *why*. CI must be
green before merge (it runs the same Makefile targets you ran locally).
