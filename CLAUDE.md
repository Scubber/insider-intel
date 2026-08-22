# CLAUDE.md — agent operating manual

Insider-risk OSINT aggregator. **This repo is in production**: UI on GitHub
Pages, API on Cloud Run, corpus in GCS, self-refreshing every 6h, CD on merge
to `main`. Read this before changing anything; deeper docs in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) (dev env) and
[docs/hosting.md](docs/hosting.md) (production).

**Picking up a session?** [docs/HANDOFF.md](docs/HANDOFF.md) is the current live
state — what's deployed now, what's parked (e.g. PACER purchasing), open threads,
and next actions — with links to recent PRs.

## Map

```
intel.thederpweb.com (Pages, web/) ──► api.intel.thederpweb.com (Cloud Run: insider-intel-api)
                                          │ GCS FUSE mount at /app/data
                       gs://insider-intel-502413-corpus  (processed/ raw/ state/ config/)
                                          ▲ read-write
DGX Spark cron (0 4,10,16,22 UTC) ──► spark_refresh.sh (refresh tenant) ──► POST /reload
  (Cloud Scheduler + Cloud Run Job corpus-refresh: PAUSED since the 2026-08-16
   cutover, kept as rollback — docs/spark-cutover-handoff.md)
```

GCP project `insider-intel-502413`, region `us-east1`, $10/mo budget alert.

## Data & classification model

Ingestion lanes (all emit `RawArticle` → `data/raw/articles.jsonl`): RSS
(`config.py::DEFAULT_FEEDS`), CourtListener, DataTheftNews, sitemap
archive, web-keyword RSS, and **social** — Reddit (`reddit_pipeline.py`, OAuth
app auth when `REDDIT_CLIENT_ID/SECRET` set, public JSON otherwise) and X
(`x_pipeline.py`, needs `X_BEARER_TOKEN` or `X_CONSUMER_KEY`/`SECRET` — the
pipeline mints the bearer; pulls are cadence-capped for the free tier via
`X_INGEST_EVERY_HOURS`). **Scheduled social pulls are parked** (operator
decision 2026-08-16, no OAuth app; `SOCIAL_INGEST_ENABLED=false` default in
`shared/settings.py` — manual `ingest_social`/`ingest_social_url` still work).
Social sources are user-picked
subscriptions (`data/config/social_subscriptions.json` — the `config/` GCS
prefix is exactly why the API may write there) seeded from a curated catalog
derived from `shared/taxonomy/use_cases.py`.

Every refresh cycle smoke-tests each configured lane:
`apps/aggregator/lane_health.py` enumerates lanes from the live config, folds
the per-source outcomes into `data/state/lane_health.json` (consecutive-failure
counts carry across cycles; ≥3 failed/empty cycles = broken, logged as
`[LANE-BROKEN]`), and the API serves it at `GET /lanes/health` for the UI's
source-health surfaces: the DATA SOURCES line in SETTINGS plus a masthead
warning chip that renders only while a lane is broken (the site footer that
used to carry the line was removed 2026-08-17; the ABOUT page at `#/about`
absorbed the old METHODOLOGY & COLOPHON pane in the same change).

CourtListener flagging is query-driven, not a local scan: the hand-authored
insider lexicon in `courtlistener.py::DEFAULT_QUERIES` (projected from ITM
techniques, e.g. the IF038 moonlighting cluster) runs server-side against US
RECAP dockets + opinions. `COURTLISTENER_COMPANY_WATCHLIST` (default `Voya,
Voya India`) adds per-company coverage — `company_watchlist_queries` expands
each name into a scoped insider query **and** a bare catch-all, appended to
both lanes. CourtListener indexes US courts only, so a non-US entity is matched
by US filings that name it, not by foreign court records.

**IndiaCourts** (`indiacourts.py` + `indiacourts_pipeline.py`,
docs/india-courts-ingest.md) is the $0 Indian filings lane: a local
insider-lexicon scan over the CC BY 4.0 eCourts open dataset (AWS Open Data,
25 High Courts, daily-updated) — parquet partition diffs → per-PDF fetch →
pypdf text extraction (OCR command hook for scans) → only matches enter the
corpus, WITH full text attached (no stub rows, so the CL backfill/clear
contract doesn't apply). Disabled by default (`INDIACOURTS_ENABLED`; heavy
work belongs on sparky); hub-courts-first history walk to the 2000 floor.
Court rows carry typed `legal_metadata` (country, court, CNR, stage);
`resolve_country` (`shared/utils/evidence.py`) maps source prefixes →
US/CA/IN as the fallback, and a `country` facet threads through
`/articles`, `/search`, `/sources`, `/feed.xml`, `/export/articles`, and the
EVIDENCE ledger (`?country=IN` recomputes the same engine over the slice).

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
queries, ITM adjudication incl. `is_insider_case`), and derives the legacy
`case_record` from it. **What "qualifying" means is the spend policy**
(`summarize.py::qualifies`) — a 2026-07 corpus audit found 66% of billed calls
were LLM-adjudicated non-cases before these gates:

- **Filings**: full body present (`clean_text ≥ SUMMARIZER_FILING_MIN_TEXT_CHARS`)
  **and** an insider signal *in the body itself* — since 2026-08 a TWO-part
  signal: an ITM alias match **and** an `INSIDER_FRAMING_KEYWORDS` hit (a lone
  alias passed company-v-company IP litigation — 58% of post-gate enrichments
  adjudicated non-insider), or a use-case / insider-alignment verdict. Ingest
  match-marker lines ("CourtListener query: …" / "IndiaCourts match: …") are
  stripped before every body check so a marker can't qualify its own document.
  The per-article `itm_hits` fire off docket metadata — which embeds the CL
  query tag — so they must never unlock a filings bill on their own. The gate
  resolves filings from `FILINGS_SOURCE_PREFIXES` (`canlii-`, `indiacourts-`)
  plus CourtListener ids — court lanes must be listed there or they fall to
  the news gate (CanLII sat in that gap until 2026-08-22).
  `scripts/replay_filings_gate.py` replays old vs new gate over a stored
  corpus (operator runs it on sparky; the false-negative report is the review
  gate for this change).
- **News**: lexical ITM technique hit with `itm_alignment=="insider"`;
  use-case framing alone never bills (vendor-commentary class).
- **Social/tips**: a classified use case suffices (first-person confessions).
- **PACER purchase eligibility** deliberately stays looser
  (`filing_requires_body=False`, metadata signal OK) — it buys bodies for
  stubs, so it can't require one.

Each article is billed once, ever — the graph carries `prior_forensics` and
the append-only `enrichment_history` forward on reprocess, and the
corpus-refresh backfill sweep converts the existing corpus gradually
(newest-first, then legacy `case_record`-only rows when
`SUMMARIZER_UPGRADE_LEGACY`), bounded by `SUMMARIZER_MAX_ARTICLES_PER_RUN`
with `SUMMARIZER_BACKFILL_RESERVE` guaranteed to the sweep. The hunt report
reads these stored records — no LLM at read time.

**Hunt synthesis** (`apps/aggregator/hunt_synthesis.py`, runs at the end of
each processing pass): one LLM call per observed technique distills its case
material (entity-filtered `hunt_terms`, method actions, artifact families,
query seeds — see `shared/utils/evidence.py::is_entity_term`) into 2–4
generalized, **tool-agnostic** hunt patterns
(`shared/schemas/hunt_patterns.py`): plain-language detect/prevent methods
spanning telemetry, process, and people (training, HR, offboarding) — never
query syntax or product names. Cached by input signature in
`data/state/technique_hunts.json` (job writes `state/`, API reads — same
contract as `technique_seeds.json`), so a technique re-synthesizes only when
its case set changes; bounded by `HUNT_SYNTH_MAX_PER_RUN` (10 in prod, chain
inherits `SUMMARIZER_LLM_PROVIDER`). Served on `/evidence/technique/{id}` as
`patterns`; the dossier renders them as "How to spot it / How to counter it"
cards (raw SIEM-style seeds stay in the stored forensics but no longer
render), and the "Copy LLM hunt prompt" feeds the patterns. Case-specific
literals (people, companies, domains) must never reach the dossier or
prompt — that's the point.

**Enrichments are append-only, never rewritten** (operator mandate):
every generation lands in `ProcessedArticle.enrichment_history`
(`shared/schemas/forensics.py::EnrichmentRecord`, deduped by signature); the
top-level `ai_summary`/`forensics` are a *projection* selected in two stages:
the `is_insider_case` **verdict** is won by whichever side's best generation
carries the higher confidence, then richness picks within that verdict
(`enrichment_richness`: analyst note + method count + confidence, ties →
newest). Richness alone can never flip an adjudication — a chatty
low-confidence generation lands in history but not the projection (critical
once multi-model sweeps write second opinions). Every method also carries a
write-time `evidence_quote_verbatim` grounding stamp (deterministic
normalized-substring check against the text the model saw; backfill via
`scripts/backfill_quote_verbatim.py`). A thin re-enrich can therefore never
gut a rich record, and
`_clear_llm_fields` intentionally does NOT clear history. If a run's LLM
attempts all produce nothing, the job logs
`[FAIL] enrichment: N LLM attempt(s), 0 records produced` — that tripwire
firing means a dead provider (missing key or $0 balance), not a quiet day.

`SUMMARIZER_LLM_PROVIDER` is an ordered fallback chain (comma-separated; each
tried until one succeeds, unfunded named entries skipped). Prod leads with
`anthropic` and pins `SUMMARIZER_MODEL=claude-haiku-4-5-20251001` — enrichment
runs on **Haiku 4.5** to fill the backlog at ~1/3 the Sonnet cost — then
`openai`/`sol`/`gemini`/`xai` as fallbacks. The chain, `SUMMARIZER_MODEL`, and
`LLM_CUSTOM_PROVIDERS` live in `deploy-api.yml` (edit + merge, not gcloud);
`SUMMARIZER_MODEL` overrides only the **first** provider, so it must be a valid
id for that vendor (an Anthropic id today). Same chain mechanics apply to
`DISCOVERER_LLM_PROVIDER` (the filings-only second call).

Provenance channels: `news | filings | tips | social | publications` — legacy
`reddit-*` RSS feeds stay `tips`; API-based social sources use `social-*` ids;
long-form reference docs (curated catalog in `publication_sources.py`, PDF
text via `publication_extract.py`) use `pub-*` ids and bypass the process
min-score gate. One-off flagging: `ingest_publication_url` CLI /
`POST /publications/ingest_url`. Facets thread
end to end: `use_case` / `insider_type` / `channel` params on `/articles`,
`/search`, `/sources`; registry at `GET /usecases`; subscriptions at
`/social/catalog` + `/social/subscriptions`; one-off flagging via
`POST /social/ingest_url` (accepts Reddit `/s/` share links). The filings
lane also carries international coverage: CanLII per-court RSS
(`canlii-*` feeds, `channel="filings"`), the IndiaCourts dataset lane
(`indiacourts-*`), plus prosecutor/regulator feeds (AFP, OAIC, CPS, NCA,
ICO, Justice Canada) in `config.py`.

The stream acts on the enricher's own verdict: rows whose stored forensics
say `is_insider_case=false` render muted with a **purpose stamp** — the
enricher's `context_kind` (DETECTION / PREVENTION / TRADECRAFT / POLICY /
NEWS, ITM control language) when stored, else a channel fallback (LEGAL
CONTEXT / NEWS / COMMUNITY / REFERENCE) for pre-`context_kind` enrichments —
and are hidden by default behind a toggle — un-enriched rows are unknown,
never context.

## EVIDENCE — the research product

Corpus-wide forensic aggregation, the flagship output of this project.
Core is `shared/utils/evidence.py` — **pure stdlib on purpose** so bare
Actions runners can load it via `importlib.util.spec_from_file_location`
without the pydantic import chain (see `scripts/evidence_ledger.py`). Served
at `GET /evidence/ledger` and `GET /evidence/technique/{id}`
(`apps/search/service.py`), rendered as the EVIDENCE page (`#/evidence`
takeover pane) with per-technique tie-ins in the dossier and workbench.
Published findings live in `web/findings.json` — **merging the authoring PR
IS operator approval**; the page renders findings even during API cold-start.

Its invariants: **roles, never individuals** (no persona/entity resolution —
permanently scrapped as PII-shaped); **adjudicated/admitted vs alleged vs
reported are never conflated** (`case_strength` — Indian pre-adjudication
stages [FIR, charge sheet, bail, quashing, interim relief, writ/disciplinary
review] are weighted below the adjudicated floor in `POSTURE_WEIGHT`;
`tests/test_legal_postures.py` is the three-site drift tripwire);
**jurisdiction = the source court system, never actor nationality** — the
EVIDENCE page renders per-nation tabs (GLOBAL | US | IN | …, one ledger
engine recomputing per `?country=` slice, basis line naming the slice,
small-n floor per slice) plus a TACTICS BY REGION cross-tab on the global
view; percentages suppressed below `SMALL_N_FLOOR`; the evidence→ITM detection crosswalk
(`EVIDENCE_DT_CROSSWALK`) stays conservative — external/legal record classes
map only to DT152 (Financial Auditing; DT067 before ITM 2.9). Color law: `--accent` = court-proven, `--signal` =
observed/alleged, always with an explicit legend.

**TOOLING** (`#/tooling`, `GET /tooling`) ranks security-tool **categories —
never vendor brands** — by how much observed insider-case volume their mapped
ITM DT/PV controls cover. The category→control taxonomy is authored and
checked in (`shared/data/tooling_map.json`, ids single-homed, validated
against the ITM catalog by `tests/test_tooling.py` so ITM refreshes flag
drift); the ranking recomputes per call from the verdict-gated ledger's
per-technique counts + `detected_by` record classes
(`apps/search/tooling.py::rank_tool_categories`, pure and deterministic), so
a sweep + `/reload` re-ranks with no redeploy. Same small-n law and color law
as EVIDENCE; the page's basis line cites the ledger's `generated_at` +
`basis` verbatim. Inside a category's expanded detail, vendors ARE named and
ranked — but only by **documented case mentions**: distinct stored documents
whose text names the product (`apps/search/vendor_mentions.py` scanning the
checked-in `shared/data/vendor_aliases.json`; alias safety rules in the file
— no common-English bare aliases, `no_safe_alias` entries never count).
Presence in the record, never effectiveness, computed once per index
generation (weak-keyed on the index object; `/reload` invalidates), and
never an input to the category ranking (byte-identity test-pinned).
The matrix–tooling alignment (2026-08-17) rides the same payload: each
category row carries its full covered-technique list (`covered_techniques`;
`top_techniques` is its head), the UI adds a per-category dossier at
`#/tooling/<category-id>`, and every technique dossier gets a RELEVANT
TOOLING section — both are client-side joins of the session-cached
`/tooling` read with the `/itm` catalog (`web/app.js::dossierToolingJoin`,
node-executed unit tests + api()-only contracts in `tests/test_tooling.py`).
The **NAMED TOOLS directory** (2026-08-17) is the pane's second list-level
view: a `CATEGORIES | NAMED TOOLS` segmented switch flips `#/tooling` ↔
`#/tools` (card grid, one card per vendor across categories, dual-homed
vendors deduped; ×0 vendors dim under a NOT YET NAMED IN CASES divider), and
`#/tools/<vendor-slug>` is the vendor sheet whose receipts are the actual
naming cases — the payload's vendor rows carry `cases` (link/title/
verdict_true/published, capped at the `VENDOR_CASE_REFS_CAP = 25` most
recent by published date) + `more_cases`. All of it renders from the one
session-cached `/tooling` read; NAMED ×N chips on category/technique
dossiers route into the sheets. Every TOOLING surface cites one muted basis
line (`BASED ON <N> VERDICT-TRUE CASES · AS OF <date>Z · METHODOLOGY ·
ITM™ Forscie Ltd`); the caveat prose lives in the METHODOLOGY tooltip
(operator call 2026-08-17). Contracts: `tests/test_tools_directory.py`.

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
python -m apps.aggregator ingest_indiacourts          # scan new Indian HC judgments (INDIACOURTS_ENABLED)
python -m apps.aggregator sweep_indiacourts_history   # walk Indian judgment years back to the 2000 floor
python -m apps.aggregator extract_indiacourts_pending # retry failed/OCR-pending judgment PDFs
python scripts/replay_filings_gate.py                 # old-vs-new spend gate over the stored corpus (run on sparky)
python -m apps.aggregator reenrich_missed --dry-run   # count filings whose forensics aren't on the current model
gcloud run jobs execute corpus-refresh --region us-east1 --wait   # force a corpus refresh
gcloud logging read 'resource.labels.job_name=corpus-refresh' --freshness=6h \
  --format='value(textPayload)' | grep -E '\[OK\]|\[FAIL\]|reloaded'
```

**No GCP access?** (Claude Code on the web sandboxes can't reach GCP or the
prod API directly.) Read-only `workflow_dispatch` diagnostics cover it —
dispatch from the Actions tab or the GitHub API: `refresh-corpus` /
`watch-refresh` (force + watch a refresh; `refresh-corpus` takes a
`force_reprocess` input that replays the job with `--force` — full lexical
retag of the corpus after an ITM/lexicon bump, no LLM re-billing), `corpus-status` (state + job env
audit: secret/env *names*, never values), `corpus-count`, `corpus-sample`,
`corpus-noninsider` (spend-waste audit), `traffic-report` (daily DIY
analytics: forensic request CSV + GeoIP, run artifacts + bucket export),
`evidence-ledger` (writes
`export/evidence-ledger.{md,json}` to the bucket), `probe-extract` (live API
round-trip), `service-logs` (Cloud Run API service errors + request 5xx).
A dispatch workflow must exist on `main` to be invokable; it then runs the
file from whatever ref you pass, so branch diagnostics work without merging.

Deploys: **merge to `main`** → `ci.yml` + `deploy-api.yml` (keyless OIDC via
Workload Identity pool `github`; no stored credentials) + `pages.yml` for
`web/**`. There is no laptop deploy step; `scripts/deploy_cloud_run.sh` is a
legacy fallback.

## Invariants — do not break

- **Everything corpus-derived is dynamic — no frozen numbers, ever**
  (operator directive 2026-08-17). Any surface built on corpus data — a tab,
  panel, finding, export — must recompute from refresh outputs: the live API
  after `/reload`, the boot snapshot at its next build, or a `state/` file
  the job rewrites each cycle. Every sweep/re-enrichment must propagate with
  no redeploy. If a tab needs its own helper endpoint, reload-invalidated
  scanner, or per-cycle state file to stay live (TOOLING's mention scanner;
  future INSIGHTS), build the helper — that cost is accepted. Checked-in
  data files are for AUTHORED taxonomy only (ITM catalog, tooling map,
  vendor aliases); a checked-in file holding corpus-derived numbers is a
  bug. Pin each new surface with contract tests in the style of
  `tests/test_matrix_data_sources.py`.
- **Every page teaches itself** (operator directive 2026-08-17). A
  user-facing surface ships WITH its explanation, in the same PR: a GUIDE
  cheat-sheet line (contract-enforced — `tests/test_site_guide.py` fails a
  bare tab), a one-line purpose sub-line on the pane itself ("what question
  does this answer"), `data-tip` tooltips on every metric, abbreviation, or
  control whose meaning isn't self-evident, and an empty state that says
  what will appear and how to make it appear. Display doctrine: numbers get
  verbs ("CAUGHT ×12", "DETECTS 64%"), never noun phrases ("corroborated
  case count"); methodology lives one hover away in the tooltip, not
  inline; runbook voice, no marketing adjectives. Modern-site UX polish is
  a requirement of done, not gold-plating.
- **UI changes pass the browser smoke before merge** (operator directive
  2026-08-17, after a broken deploy shipped). The `ui-smoke` job in `ci.yml`
  is the floor: it serves `web/` statically (no API, no snapshot) and drives
  it headless in Chromium at 390 and 1280 via `scripts/ui_smoke_ci.py` —
  zero uncaught page errors, zero console errors beyond the expected-offline
  allowlist, every masthead tab renders its pane, the GUIDE opens, TOOLING
  renders its table or its honest offline note, and deep links (`#/tooling`,
  `#/technique/<ID>`, legacy `#/tools`) don't crash. Agents with a working
  browser also run `scripts/ui_smoke.py` locally (the full journey suite
  against the preview bundle); a UI PR that can't demonstrate browser
  validation says so explicitly in its body.
- **Voice: executive-plain, never AI slop** (operator directive 2026-08-17).
  The reader is a mixed executive audience — security, business, HR, legal,
  corporate special investigations. Assume intelligence, not vocabulary.
  Short, plain sentences; one idea per sentence; concrete subject + verb
  ("the employee copied 4,000 files", not "data exfiltration was
  conducted"). Banned tells: delve, leverage, robust, comprehensive,
  seamless, holistic, landscape, utilize, "it's important to note",
  "in today's world", rhetorical-question headers, stacked qualifier
  chains. Security jargon gets a plain gloss on first use or a tooltip
  (ITM ids, DT/PV, UEBA, RECAP). Findings and analyst-facing prose read
  like a briefing memo — what happened, what it means, what to do next —
  and every generated-text template (findings slots, hunt patterns, ai
  summaries) must be written and reviewed against this bar. The read-aloud
  test: if a sentence would sound wrong spoken to a general counsel,
  rewrite it.
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
  `PACER_USERNAME`/`PACER_PASSWORD`. **Armed in prod since 2026-07-24**
  (`PACER_PURCHASE_MAX_PER_RUN=5` in `deploy-api.yml`; set 0 to re-park).
  Never add another purchase path.
- **`POST /extract/ttps` spends NO LLM credits** — it assembles each boarded
  article's stored ingest-time `forensics` record (or a floor-derived one for
  not-yet-enriched articles) into technique sections in code
  (`apps/search/ttp_extract.py`: `_mechanical_sections` + `_attach_controls`).
  Since 2026-08 it is a **forensic case study** (MODUS OPERANDI): per-case
  methods, observables, legal posture, ITM catalog controls — no hunt-query
  or seed-term surfaces (hunting guidance lives in the dossier's synthesized
  patterns; the report cross-links there). All LLM spend lives on the
  **corpus-refresh job** (the enrich node), never the API service — keep
  extract-time keys off the service. The rate limiter
  (`apps/search/ratelimit.py`) stays as a CPU/abuse guard only; don't remove
  it. Rollout: enrichment backfills over refreshes, so reports get richer over
  time and are never empty (floor fallback).
- **Match-signal text goes in `RawArticle.content`, never `summary`** —
  summaries render in the UI; `content` is scored but hidden (see the
  CourtListener query-tag fix).
- **Enrichment history is append-only.** Never overwrite or clear
  `enrichment_history`; the top-level LLM fields are a select-best
  projection, not the storage. "I don't want rewrites. I want data stored."
- **The EVIDENCE product reports roles, never individuals**, and never
  conflates adjudicated with alleged. No persona graphs, no entity
  resolution across cases.
- **Service resources and job secret mappings are asserted in
  `deploy-api.yml`** (`--memory`, `--task-timeout`, `--update-secrets`) so
  every deploy self-heals them. Change them there, by merge — never with
  ad-hoc gcloud.
- **Write/ops endpoints are token-gated in prod** (`ADMIN_API_TOKEN`):
  `/reload` (the OOM-fatal index swap), subscription writes, and both
  `ingest_url` endpoints require the bearer when the token is set — unset
  stays open for local dev. The secret maps to both the service (verify) and
  the job (call). The read product stays anonymous. Don't add new write or
  compute-heavy endpoints without this dependency.
- Secrets: Secret Manager / env only. `detect-secrets` hook + baseline are
  enforced via pre-commit (`make precommit`).
- Actions in workflows are **SHA-pinned**; keep it that way.

## Verification habits

- **Every PR gets verified before merge, and the PR body says how** (the PR
  template carries the checklist): a `web/**` change gets a **Playwright
  drive/screenshot** — including the responsive widths it touches (phone ~390,
  iPad portrait 768 / landscape 1024, desktop) — plus `scripts/ui_smoke.py`;
  a code change gets `pytest`/`ruff`; a workflow change gets a dry-run or
  dispatch. Skipping a check is fine, saying so is not optional. CI runs
  `scripts/ui_smoke_ci.py` (the `ui-smoke` job) on every `web/**` PR as the
  required browser floor — local sandboxes often lack browsers, so the gate
  lives where a browser always exists.
- `.mcp.json` provides **Playwright MCP** (official Docker image,
  `--network=host`) — use it to drive/screenshot local (:5500/:8000) or prod
  UI — and the **Cloudflare MCP servers** (`mcp.cloudflare.com` full API via
  Code Mode, `docs.mcp.cloudflare.com` docs). Cloudflare MCP is
  OAuth-gated: authenticate once via `/mcp` in an interactive session
  (headless/remote sessions can't complete the flow — they fall back to the
  `CLOUDFLARE_API_TOKEN`-driven `dns/**` workflows, which remain the
  merge-audited path for DNS record changes regardless). For the full
  platform skills, run `/plugin marketplace add cloudflare/skills` +
  `/plugin install cloudflare@cloudflare` locally (per-user, not repo
  config). Physical-click testing catches what curl can't (see gotchas). The
  responsive rail collapses below **1024px** into the INSIGHTS tab, so test
  tablet widths, not just desktop.
- `deploy-api.yml` smoke-tests `/health`, `/articles`, `/social/catalog`,
  `/trending`, `/feed.xml`, `/evidence/ledger`, a subscription write
  round-trip, and `/extract/ttps` after every deploy. Extend it when adding
  endpoints with side effects.
- After lexicon/taxonomy/feed changes: `process --force`, then `POST /reload`
  (locally automatic on next request; prod via the refresh job or curl).

## Hard-won gotchas

- **The shipped UI has NO offline-responder/demo code** (`web/demo/` is gone;
  `scripts/ui_smoke.py` enforces the ban) — but it DOES boot from a
  **first-paint snapshot**: `pages.yml` generates `web/data/` into the Pages
  artifact (scheduled ≈40min after each refresh; never committed — the smoke
  guard asserts that) and `boot()` paints the stream from it with a **CACHED**
  badge while `probeLiveApi()` backs off ~75s through the Cloud Run cold
  start, then live data replaces it and the badge flips LIVE. CACHED never
  claims LIVE; a hard error state appears only when there is no snapshot AND
  no API. The only true offline build remains the separate single-file
  preview (`scripts/export_preview.py`).
- **Burst 503s across ALL endpoints at identical timestamps = instance
  OOM-kill, not an app bug** ("connection to the instance had an error" in
  Cloud Run logs; `/health` keeps working because it's tiny). `/reload` is
  the fatal spike — it holds old + new index simultaneously. Memory is
  asserted in `deploy-api.yml` (2Gi as of 2026-07); it must grow with the
  corpus. Diagnose with the `service-logs` dispatch workflow.
- **Never run manual `--set-secrets` on the job** — it REPLACES the whole
  mapping set and silently dropped the ANTHROPIC/OPENAI/COURTLISTENER keys
  once, killing enrichment for two days (the provider chain skips keyless
  providers with no log). `deploy-api.yml` re-asserts all mappings with
  merge-semantics `--update-secrets` on every deploy; verify with the
  `corpus-status` env audit.
- **Job task-timeout must exceed a full-throughput run**: at 45m a heavy
  run hit the timeout and Cloud Run's retry re-billed already-enriched
  articles (history select-best absorbed it, but it doubled spend). Now 60m
  in `deploy-api.yml`, with the `watch-refresh` watcher at 75m.
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
**Docs ride the same PR**: a change that alters architecture, invariants,
ops knobs, or live state updates CLAUDE.md and/or docs/HANDOFF.md in that
same PR — never "later". HANDOFF's Last-updated date is the freshness
tripwire; the PR template carries the checklist.
