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

## Data & classification model

Ingestion lanes (all emit `RawArticle` → `data/raw/articles.jsonl`): RSS
(`config.py::DEFAULT_FEEDS`), Feedly, CourtListener, DataTheftNews, sitemap
archive, web-keyword RSS, and **social** — Reddit (`reddit_pipeline.py`, OAuth
app auth when `REDDIT_CLIENT_ID/SECRET` set, public JSON otherwise) and X
(`x_pipeline.py`, needs `X_BEARER_TOKEN` or `X_CONSUMER_KEY`/`SECRET` — the
pipeline mints the bearer; pulls are cadence-capped for the free tier via
`X_INGEST_EVERY_HOURS`). Social sources are user-picked
subscriptions (`data/config/social_subscriptions.json` — the `config/` GCS
prefix is exactly why the API may write there) seeded from a curated catalog
derived from `shared/taxonomy/use_cases.py`.

Processing (`shared/agents/article_processor.py`, LangGraph):
normalize → extract_entities (ITM alias match) → score → **classify** →
**enrich** → embed → assemble. The classify node stamps `use_cases`
(overemployment, data-exfiltration, credential-misuse, shadow-it) and
`insider_type` (malicious | negligent | unintentional) via heuristics in
`shared/utils/classify.py`; an optional LLM refiner
(`CLASSIFIER_LLM_PROVIDER=anthropic|openai`, `shared/llm/`) sharpens
low-confidence social posts. A classified use case + insider type upgrades
weak ITM alignment so first-person confessions surface under Insider Focus.

The **enrich** node (`shared/agents/summarize.py`, `SUMMARIZER_LLM_PROVIDER`)
makes **one unified LLM call per qualifying article** that produces the analyst
note (`ai_summary`), a full forensic record (`ProcessedArticle.forensics`:
actions with tools/quantities, observables typed by channel, per-case hunt
queries, ITM adjudication), and derives the legacy `case_record` from it. Each
article is billed once, ever — the graph carries `prior_forensics` forward on
reprocess, and the corpus-refresh backfill sweep converts the existing corpus
gradually (newest-first, then legacy `case_record`-only rows when
`SUMMARIZER_UPGRADE_LEGACY`), bounded by `SUMMARIZER_MAX_ARTICLES_PER_RUN`. The
hunt report reads these stored records — no LLM at read time.

Provenance channels: `news | filings | tips | social | publications` — legacy
`reddit-*` RSS feeds stay `tips`; API-based social sources use `social-*` ids;
long-form reference docs (curated catalog in `publication_sources.py`, PDF
text via `publication_extract.py`) use `pub-*` ids and bypass the process
min-score gate. One-off flagging: `ingest_publication_url` CLI /
`POST /publications/ingest_url`. Facets thread
end to end: `use_case` / `insider_type` / `channel` params on `/articles`,
`/search`, `/sources`; registry at `GET /usecases`; subscriptions at
`/social/catalog` + `/social/subscriptions`; one-off flagging via
`POST /social/ingest_url` (accepts Reddit `/s/` share links).

## Everyday commands

```bash
make up / down / shell / logs      # local stack: API :8000, UI :5500, Postgres :5432
make test / lint / fmt / precommit # same commands CI runs — green local == green CI
python -m apps.aggregator social suggest|add|remove   # manage social subscriptions
python -m apps.aggregator ingest_social               # pull subscribed Reddit/X sources
python -m apps.aggregator ingest_social_url <url>     # flag one post (handles /s/ links)
python -m apps.aggregator backfill_courtlistener_text # pull full RECAP/opinion bodies for stored cases
python -m apps.aggregator purchase_pacer --dry-run    # preview PACER buys (RECAP Fetch, budget-capped)
python -m apps.aggregator sweep_courtlistener_history --windows 4  # pull historical case windows manually
python -m apps.aggregator reenrich_missed --dry-run   # count filings whose forensics aren't on the current model
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
- **PACER purchases spend real money** — only via `pacer_purchase.py`
  (CourtListener RECAP Fetch), only insider-qualifying cases after the free
  archive came up empty, capped by `PACER_PURCHASE_MAX_PER_RUN` and
  `PACER_QUARTERLY_BUDGET_CENTS` (default $27/quarter — under PACER's $30
  fee waiver, so typical usage bills nothing). No-op without
  `PACER_USERNAME`/`PACER_PASSWORD`. Never add another purchase path.
- **`POST /extract/ttps` spends NO LLM credits** — it assembles each boarded
  article's stored ingest-time `forensics` record (or a floor-derived one for
  not-yet-enriched articles) into technique sections in code
  (`apps/search/ttp_extract.py`: `_mechanical_sections` + `_attach_controls` +
  `_aggregate_hunt_queries` + `_derive_legacy_fields`). All LLM spend lives on
  the **corpus-refresh job** (the enrich node), never the API service — keep
  extract-time keys off the service. The rate limiter
  (`apps/search/ratelimit.py`) stays as a CPU/abuse guard only; don't remove
  it. Rollout: enrichment backfills over refreshes, so reports get richer over
  time and are never empty (floor fallback). The client-side "copy LLM prompt"
  is the escape hatch for cross-case narrative synthesis.
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
  from GCP until OAuth creds exist. X ingest needs `X_BEARER_TOKEN` or the
  consumer key/secret pair; free-tier reads (~100 posts/mo) are protected by
  the 48h default cadence + 5-post pulls — don't loosen without a paid tier.
- **GHA runners run containers with a different uid** than the checkout owner:
  tests must write only under `tmp_path` / patched settings paths (see
  `tests/test_extract_rate_limit.py`).
- Ubuntu's bare `docker.io` lacks BuildKit; the Dockerfile uses heredocs, so
  `apt install docker-buildx` is required (compose builds are fine).

## Branch/PR conventions

Work on branches; `main` deploys. Commit messages explain *why*. CI must be
green before merge (it runs the same Makefile targets you ran locally).
