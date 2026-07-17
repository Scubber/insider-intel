# insider-intel - Architecture (MVP)

## Project Overview
**insider-intel** is a **multi-domain insider-risk** OSINT aggregator under the
**thederpweb** brand (HR / legal / business risk / infosec / enterprise risk — not
infosec-only). Primary job: pull public OSINT → map to **Insider Threat Matrix™
(ITM)** techniques → mint **hunt / detection keywords**.

Insider Threat Matrix™ is owned by Forscie Limited. This product is aligned /
mapped to ITM; it is not an official or endorsed Forscie product. See `NOTICE`.

### Scope
- **MVP (now):** Multi-domain RSS (infosec + HR/legal) + CourtListener RECAP +
  optional Google Alerts (`WEB_KEYWORD_FEED_URLS`) / Feedly; LangGraph processing
  (ITM match + score); JSONL storage; article stream + ITM filters + search API;
  static UI + operator-term workbench; **one-way corporate export**. **Develop
  locally; host later.** Sourcing runbook: [`docs/sourcing.md`](sourcing.md).
  News seat budget: prefer **$0**; hard cap **&lt;$100/year** total (no Law360).
- **Later:** expand archive sources, Postgres + pgvector, LLM summaries / ITM classification, scheduled ingest, Cloudflare Pages + Cloud Run deploy.
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
- CourtListener RECAP search for federal dockets (no PACER purchase)
- Optional Feedly boards and Google Alerts-style RSS (`WEB_KEYWORD_FEED_URLS`);
  prefer Alerts for cross-domain discovery (see [`sourcing.md`](sourcing.md))
- Future: site scrapers emitting the same `RawArticle` schema
- Sitemap archive backfill (`ingest_archive`) for keyword-filtered history
- Stores raw articles + metadata
- Corporate consumers pull via `aggregator export` or `GET /export/articles` (never inbound corp APIs)

### 2. Processing Layer
- LangGraph: normalize → extract entities → score → embed → assemble
- Entity extraction matches article text to ITM technique titles + curated aliases
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
- Secondary stream controls: Insider Focus / All indexed + Channel + Source
- Corporate export NDJSON includes the same handoff fields

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

Extract reads indexed title/summary/text (plus best-effort CourtListener REST
snippets for filings). With `XAI_API_KEY`, xAI fills the same channel slots and
merges onto the curated IF038 seed floor. Without a key (or on API failure),
Extract returns the seed pack and labels the report honestly.

**Still later:** full multi-article CTI markdown template, server-side flags,
persisted `ai_summary` on every article, social tip ingest beyond Reddit RSS.

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
