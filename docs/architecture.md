# insider-intel - Architecture (MVP)

## Project Overview
**insider-intel** is a **multi-domain insider-risk** OSINT aggregator under the
**thederpweb** brand (HR / legal / business risk / infosec / enterprise risk — not
infosec-only). Primary job: pull public OSINT → map to **Insider Threat Matrix™
(ITM)** techniques → mint **hunt / detection keywords**.

Insider Threat Matrix™ is owned by Forscie Limited. This product is aligned /
mapped to ITM; it is not an official or endorsed Forscie product. See `NOTICE`.

### Scope
- **Now (hosted):** Multi-domain RSS (infosec + HR/legal) + CourtListener RECAP +
  DataTheftNews + optional Google Alerts (`WEB_KEYWORD_FEED_URLS`) / Feedly;
  **social ingest** (Reddit OAuth/JSON + X API v2, user-picked subscriptions,
  `channel=social`); LangGraph processing (ITM match + score + **use-case /
  insider-type classification**, heuristic with optional LLM refiner); JSONL
  storage; article stream + ITM / use-case / insider-type filters + search API;
  static UI + operator-term workbench; **one-way corporate export**. Production
  hosting per [`hosting.md`](hosting.md). Sourcing runbook:
  [`docs/sourcing.md`](sourcing.md). News seat budget: prefer **$0**; hard cap
  **&lt;$100/year** total (no Law360).
- **Later:** expand archive sources, Postgres + pgvector.
- Product maturity (auth, read/unread, detection generation pipelines, etc.) stays out of MVP.
- **Corporate boundary:** export OSINT *out* only; never read Graph/Teams/email/SIEM from this repo.

## Goals & Constraints
- Keep it **cheap** to run and **easy to maintain**
- **Local-first development**; design for hosting without rewriting
- Prefer managed/serverless services when deploying
- Easy to add RSS sources (`DEFAULT_FEEDS` or `--feeds-file`)
- Handle foreign national data responsibly (cloud processing preferred when that becomes real)

## Tech Stack (MVP)

| Layer | Technology | Notes |
|-------|------------|-------|
| **Development** | Cursor + local Python 3.12+ | This repo |
| **Agent Framework** | LangGraph | Article processor |
| **Taxonomy** | Slim ITM index | Derived from forscie/insider-threat-matrix JSON |
| **Ingestion** | feedparser + httpx | RSS, CourtListener Search API, optional Feedly / alert RSS |
| **Storage (MVP)** | JSONL | `data/raw`, `data/processed` |
| **Storage (target)** | PostgreSQL + pgvector | Neon or Supabase |
| **API** | FastAPI | `/articles`, `/itm`, `/sources`, `/search`, `/export/articles` |
| **Public UI** | Static site in `web/` | Local now; Cloudflare Pages later |
| **Compute (target)** | Google Cloud Run | API + ingest jobs |
| **CI/CD** | GitHub Actions | Monorepo workflow |

## High-Level Architecture

### 1. Ingestion Layer
- Pulls RSS / Atom feeds (easy add via config or JSON)
- CourtListener RECAP search for federal dockets (no PACER purchase), plus a
  bounded **full-text backfill** each run: stored cases whose `content` is only
  the ingest query tag pull whole document bodies from the free RECAP archive
  (`recap-documents`, `is_available=true`) or the opinion cluster detail —
  capped by `COURTLISTENER_RECAP_TEXT_MAX_CHARS` (40k/docket) and
  `COURTLISTENER_BACKFILL_MAX_DOCKETS` (25 attempts/run); dockets with no
  archived text yet are retried weekly. Enriched rows get a force-refresh
  (fresh `ingested_at`) and their prior LLM fields cleared, so the next
  processing pass re-scores and re-extracts them over the full filing.
- **Rolling historical sweep**: each refresh also ingests one time window
  (`COURTLISTENER_HISTORY_WINDOW_DAYS`, default 90) of past insider-crime
  cases — insider trading, trade secrets, economic espionage, computer
  fraud — walking a persisted cursor backward from today to
  `COURTLISTENER_HISTORY_FLOOR` (default 2015-01-01; empty disables).
  Metadata only; the text backfill and purchaser pick the documents up over
  subsequent runs. A throttled window never advances the cursor. At 4
  refreshes/day the decade seeds in ~10 days;
  `sweep_courtlistener_history --windows N` fast-forwards manually.
- **PACER purchasing (opt-in)**: when `PACER_USERNAME`/`PACER_PASSWORD` are
  set, insider-qualifying cases whose documents aren't in the free archive
  get their lead document (docket report first, then complaint/indictment)
  bought via CourtListener's RECAP Fetch API — capped per run (5) and per
  quarter ($27 estimated, under PACER's $30 fee waiver → typically $0
  billed). Purchases land in the public RECAP archive (enriching the
  commons); the text backfill harvests them on a later refresh.
  `purchase_pacer --dry-run` previews what would be bought.
- Optional Feedly boards and Google Alerts-style RSS (`WEB_KEYWORD_FEED_URLS`);
  prefer Alerts for cross-domain discovery (see [`sourcing.md`](sourcing.md))
- **Social lane (`channel=social`)**: Reddit subreddit listings (OAuth app auth
  when `REDDIT_CLIENT_ID/SECRET` set; public JSON fallback) and X handles
  (API v2 — `X_BEARER_TOKEN`, or `X_CONSUMER_KEY`/`X_CONSUMER_SECRET` from
  which the pipeline mints and caches an app-only bearer. Free-tier quota
  guard: pulls run at most every `X_INGEST_EVERY_HOURS` (48h default) at
  `X_MAX_RESULTS` (5) posts per handle ≈ 75 post-reads/month, under the
  ~100/month free cap). Sources are user-picked subscriptions
  (`data/config/social_subscriptions.json`) seeded from a curated per-use-case
  catalog (`shared/taxonomy/use_cases.py` → `social_catalog.py`); single posts
  flag in via `ingest_social_url` / `POST /social/ingest_url` (handles `/s/`
  share links)
- Sitemap archive backfill (`ingest_archive`) for keyword-filtered history
- Stores raw articles + metadata
- Corporate consumers pull via `aggregator export` or `GET /export/articles` (never inbound corp APIs)

### 2. Processing Layer
- LangGraph: normalize → extract entities → score → **classify** → embed → assemble
- Entity extraction matches article text to ITM technique titles + curated aliases
- Classify stamps `use_cases` (overemployment / data-exfiltration /
  credential-misuse / shadow-it) + `insider_type` (malicious / negligent /
  unintentional): always-on heuristics (`shared/utils/classify.py`, reuses ITM
  aliases) plus an optional LLM refiner (`CLASSIFIER_LLM_PROVIDER=anthropic`
  → Claude Haiku, or `openai` → any OpenAI-compatible endpoint incl. local
  Ollama), gated to low-confidence articles on `CLASSIFY_LLM_CHANNELS`
  (default `social`). Classified use case + insider type upgrades weak ITM
  alignment so first-person confession posts surface under Insider Focus
- Each matched technique expands to linked **Detections (DT\*)** and **Preventions (PV\*)**
  from the upstream Forscie JSON (technique → control join). Articles are **not**
  keyword-matched against detection description text (Event IDs rarely appear in news).
- Outputs `ProcessedArticle` with `itm_hits`, `related_detections` / `related_preventions`,
  and operator search terms for Teams/email/SIEM paste

### 3. Storage Layer
- **MVP:** JSONL + in-memory index
- **Target:** managed Postgres + pgvector
- Slim taxonomy committed at `shared/data/itm_index.json` (refresh via `refresh_itm`);
  includes per-technique `detections` / `preventions` `{id, title}` refs

### 4. Reader / Hunt Layer
- Chronological stream (`GET /articles`) with optional `theme` / `itm_id` /
  `detection_id` / `prevention_id` + `topic_match` filters
- ITM catalog (`GET /itm`) for taxonomy + technique `article_count`
  (optional `source_id` / `channel` so counts match Refine filters)
- Keyword / semantic / hybrid search (`GET|POST /search`)
- Secondary stream controls: Insider Focus / All indexed + Channel
  (news | filings | tips | social) + **Insider type** + **use-case chips**
  (`use_case` / `insider_type` params thread through `/articles`, `/search`,
  `/sources`; registry at `GET /usecases`)
- Social discovery/subscriptions: `GET /social/catalog`,
  `GET/POST/DELETE /social/subscriptions`, `POST /social/ingest_url`
  (UI: Refine → Social sources panel)
- Corporate export NDJSON includes the same handoff fields (`use_cases`,
  `insider_type`; schema `insider-intel.export.v2`)

#### UI presentation contract (responsive web)

- **Desktop (≥960px):** Hunt-first two panes — **Articles | Workbench**. Matrix is
  a top tab (not a permanent left sidebar); selecting a technique returns to Articles.
- **Mobile (&lt;960px):** one pane via the same top tabs; Matrix selection may
  auto-switch to Articles; Workbench opens from an article. Same FastAPI / demo
  JSON contract for every client (future App Store SwiftUI can reuse these endpoints).
- **Matrix role:** taxonomy browse + availability (`[N]`), not a second article reader.
- **Count semantics:** technique `article_count` and Matrix click results honor
  active **Source + Channel**. Matrix discovery stays `itm_alignment=all` +
  `min_score=0` + `topic_match`; Insider Focus applies to the main Articles
  stream only.
- **Hunt research path:** any insider-risk term → Maps-to ITM tags (curated
  aliases in `shared/itm/aliases.py`) → articles for those tags (plus hybrid
  keyword hits). While Hunt is active, Matrix shows the mapped subset
  (CoverageFirst). Empty Maps-to for a real phrase → add aliases, don't invent
  techniques.
- **Layout experiments:** DensityList (default browse) | CoverageFirst (Hunt)
  behind one path; BoardLite / ThemeRail deferred.
- **Mobile constraint on experiments:** ThemeRail = chips + one scrolling list
  (never five side-by-side columns on phone). CoverageFirst stays a single
  column. No layout may require simultaneous Matrix + Articles on mobile.

### 4b. Story clusters (presentation)

Storage stays **one row per URL**. At process time each article gets a `story_key`
(normalized title + UTC publish day). `/articles?group=1` (default) collapses
matching rows **within the same channel** into a stream card: primary source +
sibling chips. Workbench still opens one concrete article (switchable sibling).
Hunt / search stays flat for ranking. Future Hunt Package can cite cluster
members without merging storage.

### 5. Hunt Package / Extraction board

**Workbench** is the TTP extraction surface (investigator hunts across email /
chat / network / human — not SIEM-only).

**Extraction board** (first Hunt Package slice, in `web/`):

```text
Flag from Articles stream (“+” on card)
  → Extraction board (localStorage)
  → Extract TTPs (POST /extract/ttps)
  → Workbench hunt report (channel-grouped cues)
  → Optional: Copy agent brief → CourtListener MCP in Cursor
```

Extract reads indexed title/summary/text plus the persisted `case_record`
(and best-effort CourtListener REST snippets for filings). The seed floor is
evidence-first: behaviors come from the selection's own ITM hits and
case-record methods; the curated IF038 overemployment pack is emitted only
when IF038 actually matched, or as an explicitly-labeled generic fallback for
evidence-free selections. With `XAI_API_KEY`, xAI fills the same channel slots
and merges on top.

**Ingest summarizer (opt-in).** Setting `SUMMARIZER_LLM_PROVIDER` — an ordered,
comma-separated provider chain (each tried until one succeeds; unfunded entries
skipped) — adds a `summarize` node to the processing graph (after `classify`).
The prod chain leads with `moonshot` (Moonshot AI's Kimi K2, an
OpenAI-compatible custom provider defined in `LLM_CUSTOM_PROVIDERS`) and falls
back to `anthropic`; see [hosting.md](hosting.md) for the full chain and the
`SUMMARIZER_MODEL` first-provider caveat. One LLM
call per qualifying article (has ITM hits or a classified use case) writes a
2-4 sentence `ai_summary`, a structured `case_record` (actor role, access
vector, motive signals, methods, exfil channels, timeframe, detection trigger,
outcome, confidence — court filings get the larger
`SUMMARIZER_FILINGS_MAX_INPUT_CHARS` prompt budget, default 24k, so whole
complaints/indictments are read), and adjudicates a shortlist of candidate ITM techniques
(lexical hits + nearest by hashing-embedding similarity) — accepted refs merge
into `entities.itm_hits` with `source: "llm"`, so the rail/matrix/facets light
up without UI changes. Cost controls: `SUMMARIZER_MAX_ARTICLES_PER_RUN`
(default 15) caps each run, results persist in the corpus and carry forward
through re-processing (`--force` included) so each article is billed once,
ever, and a bounded newest-first backfill sweep converts the pre-existing
corpus over successive 6h refreshes.

**Still later:** full multi-article CTI markdown template, server-side flags.
(Social ingest beyond Reddit RSS shipped — see Ingestion Layer above.)

### 6. Hosting (develop here → deploy later)

See [`docs/hosting.md`](hosting.md) for the full checklist.

- **Dev:** `python scripts/launch_local.py` (API `:8000` + static UI `:5500`)
- **Public UI:** `https://intel.thederpweb.com` (GitHub Pages demo now; Cloudflare Pages optional later)
- **Public API (later):** `https://api.intel.thederpweb.com` (Cloud Run) — DNS via Route 53 on `thederpweb.com`
- **API (local):** FastAPI with JSONL/DB; set `CORS_ORIGINS` + `web/config.js` (`INSIDER_INTEL_API_BASE`)
- **CI/CD:** GitHub Actions for `pytest` / Pages deploy. Branch tags are save points only.
- The live product requires the API; the standalone `preview/` bundle is the only offline build (share/demo only, not deployed)

## Repository Structure

```
insider-intel/
├── apps/aggregator/     # ingest / process / all / refresh_itm
├── apps/search/         # FastAPI + index + CLI
├── shared/              # schemas, agents, itm/, utils, settings
├── shared/data/         # itm_index.json (slim taxonomy)
├── web/                 # static UI (Pages-ready)
├── docs/
├── tests/
└── data/                # local JSONL (gitignored content)
```
