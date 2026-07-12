# Cursor Handoff — insider-intel

**Read this first** when continuing work on `insider-intel`.  
Then follow: `.cursor/rules/project-rules.md` and `docs/architecture.md`.

**Last updated:** 2026-07-12  
**Repo location:** `thederpweb/insider-intel/` (package lives inside the monorepo)  
**MVP status:** Backend + static Matrix/Articles/Workbench UI, **ITM-aligned tagging**. **Develop locally; host later** — see `docs/hosting.md`. Public demo UI: `https://intel.thederpweb.com`. Experiment UI: `exp/responsive-ui`.

---

## What this project is

**Insider-threat OSINT aggregator** (Feedly-style articles) whose job is to map public reporting to the [Insider Threat Matrix™](https://insiderthreatmatrix.org/) and mint **operator search terms** (Teams/email/SIEM paste) plus ITM filter tags.  
Articles join to **Detections (DT\*)** / **Preventions (PV\*)** via matched techniques (not by scanning news for Event IDs).  
**Workbench** = TTP extraction surface (operator terms + channel hunt cues).  
**Extraction board** = flag via Articles **+** or Workbench → **Extract TTPs**
(`POST /extract/ttps`, optional `XAI_API_KEY`) → channel hunt report.  
**Copy agent brief** = Cursor + CourtListener MCP deep path. First Hunt Package
slice — see architecture §5.
**Layout (POC):** Hunt + Matrix refine + Articles + Workbench; secondary Insider Focus / Source.  
Brand / product title in UI: **insider-intel**. Goals: cheap, low-maintenance, serverless-friendly, LangGraph-based agents.

Not a general cyber news Feedly. Sitemap archive backfill (`ingest_archive`)
covers keyword-filtered history where RSS cannot; follow the **New Source
checklist** in [`docs/sourcing.md`](sourcing.md). **No native Twitter/X ingest
yet** (Feedly/alert RSS only for social-adjacent content).

Insider Threat Matrix™ is owned by Forscie Limited — see `NOTICE`. Descriptive “aligned / mapped to ITM” language only; no official/endorsed claims.

### Hunt Package (future — do not build in POC)

CTI-style analyst brief assembled from **multiple flagged articles** + workbench fields:

1. Flag articles “for hunt”
2. Export multi-article JSON/markdown bundle (AI-consumable)
3. Fill Hunt Package markdown template (news seen → hunts / optional SIEM dialect / novel TTPs); AI polish later
4. Optional social/X or Feedly tip-account ingest → same `RawArticle` schema

Full write-up: `docs/architecture.md` § Hunt Package.

---

## Current state (what works)

| Capability | Status | How |
|------------|--------|-----|
| RSS multi-source ingest (easy add) | ✅ | `DEFAULT_FEEDS` or `--feeds-file` (crypto feeds disabled by default) |
| Tip / Reddit RSS (`channel=tips`) | ✅ | `reddit-*` feeds + `feeds.tips.example.json`; UI Channel pills |
| Story clusters (`story_key`) | ✅ | `/articles` groups same-day multi-source within channel |
| Feedly board / AI Feed ingest | ✅ | `FEEDLY_ACCESS_TOKEN` + `FEEDLY_STREAM_IDS` → `ingest_feedly` |
| CourtListener RECAP | ✅ | `ingest_courtlistener` (optional `COURTLISTENER_API_TOKEN`) |
| Web keyword alert RSS | ✅ | `WEB_KEYWORD_FEED_URLS` → `ingest_web_keywords` |
| Full pipeline | ✅ | `python -m apps.aggregator all` (RSS + optional sources) |
| Slim ITM taxonomy | ✅ | `shared/data/itm_index.json` via `refresh_itm` (includes DT/PV refs) |
| LangGraph processing | ✅ | normalize → ITM match → DT/PV join → score → embed → assemble |
| SIEM / control handoff | ✅ | `related_detections` / `related_preventions` on articles + export |
| Relevance gate | ✅ | `PROCESS_MIN_SCORE` (default 0.15); UI sends matching `min_score` |
| Strict ITM stream | ✅ | Default `itm_alignment=insider` (ITM-aligned scenarios only) |
| JSONL storage | ✅ | `data/raw/`, `data/processed/` |
| Article stream API | ✅ | `GET /articles` (+ `theme`, `itm_id`) |
| ITM catalog API | ✅ | `GET /itm` |
| Sources API | ✅ | `GET /sources` |
| Keyword / semantic / hybrid search | ✅ | CLI + `GET\|POST /search` |
| One-way corporate export | ✅ | `aggregator export` + token-gated `GET /export/articles` |
| CORS (env-driven) | ✅ | `CORS_ORIGINS` |
| Static UI (Hunt + Matrix + Articles + Workbench) | ✅ | `web/` — `exp/responsive-ui` POC shell |
| Web scrapers (non-RSS) | ✅ | `ingest_archive` sitemap keyword backfill (HR/legal first) |
| Native Twitter/X ingest | ❌ | Feedly / alert RSS only for now |
| Hunt Package (multi-article CTI brief) | 🟡 | Stream `+` → board → `POST /extract/ttps`; full package later |
| Postgres + pgvector | ❌ | Settings stub (`DATABASE_URL`) |
| LLM summarization / package AI | 🟡 | xAI optional on Extract (`XAI_API_KEY`); `ai_summary` persist later |
| Live Cloudflare / Cloud Run deploy | ❌ | Documented target only |
| Corp Graph/Teams/SIEM inbound | ❌ | **Never** — export only |

### End-to-end flow

```
RSS feeds
  → apps.aggregator (httpx + feedparser)
  → data/raw/articles.jsonl
  → LangGraph article_processor (ITM technique match)
  → data/processed/articles.jsonl
  → apps.search (in-memory index)
  → FastAPI /articles /itm /sources /search
  → web/ static UI (local now; Pages later)
```

---

## How to run (local — primary)

```bash
cd insider-intel
pip install -e ".[dev]"

python -m apps.aggregator all
python scripts/launch_local.py   # API :8000 + UI :5500, opens browser
# or separately:
# python -m apps.search serve
# python -m http.server 5500 --directory web
```

**Env:** copy `.env.example` → `.env`.  
**Python:** `>=3.12`.  
**CI:** `thederpweb/.github/workflows/insider-intel-ci.yml`.

### Useful CLI flags

```bash
python -m apps.aggregator ingest --feeds-file apps/aggregator/feeds.example.json -v
python -m apps.aggregator ingest --feeds-file apps/aggregator/feeds.insider_board.example.json
python -m apps.aggregator ingest_feedly
python -m apps.aggregator ingest_courtlistener
python -m apps.aggregator ingest_courtlistener --max-pages 3
python -m apps.aggregator ingest_web_keywords
python -m apps.aggregator ingest_archive --source hrdive --max-urls 50
python -m apps.aggregator process --force
python -m apps.aggregator export --out dist/export
python -m apps.aggregator refresh_itm
python -m apps.search query "exfiltration" --mode hybrid --json
```

**Feedly:** set `FEEDLY_ACCESS_TOKEN` and comma-separated `FEEDLY_STREAM_IDS` for boards
like *Insider Threats x Top Stories* / *ITM-Hunt*. Without them, `all` still runs RSS.

**CourtListener:** optional `COURTLISTENER_API_TOKEN`; defaults to built-in insider-legal RECAP queries.
Agentic research (separate from ingest): CourtListener **MCP** at
`https://mcp.courtlistener.com` (OAuth) — see
[FLP wiki](https://wiki.free.law/c/courtlistener/help/api/mcp/model-context-protocol-mcp-server-for-agentic-access)
and [`docs/sourcing.md`](sourcing.md) § CourtListener MCP.

**Web keywords:** set `WEB_KEYWORD_FEED_URLS` to Google Alerts (or similar) RSS URLs.

**Corporate export:** CLI package or `EXPORT_API_TOKEN` + `GET /export/articles`. Never configure corp Graph/SIEM credentials into this repo.

### API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Status + index size |
| GET | `/sources` | Enabled feed sources |
| GET | `/itm` | Slim ITM catalog (themes + sections) |
| GET | `/articles?limit=&source_id=&min_score=&theme=&itm_id=&itm_alignment=` | Chronological stream (default `itm_alignment=insider`) |
| GET | `/search?q=&mode=&limit=&theme=&itm_id=&itm_alignment=` | Search (default ITM-aligned only) |
| POST | `/search` | Body: `SearchRequest` |
| POST | `/extract/ttps` | Extraction-board hunt report (seeds + optional xAI) |
| GET | `/export/articles` | One-way corporate pull (Bearer `EXPORT_API_TOKEN`) |
| POST | `/reload` | Reload processed JSONL |

### Hosting later (do not block local MVP)

| Piece | Target | Config |
|-------|--------|--------|
| `web/` | Cloudflare Pages | Set `INSIDER_INTEL_API_BASE` in `web/config.js` (or inject) |
| FastAPI | Google Cloud Run | Same app; set `CORS_ORIGINS` to Pages origin |
| Ingest | Cloud Run job / GH Actions cron | `aggregator all` |

No secrets in `web/`.

---

## Key files

| File | Why |
|------|-----|
| `docs/CURSOR_HANDOFF.md` | This file |
| `.cursor/rules/project-rules.md` | Coding / security rules |
| `docs/architecture.md` | Architecture |
| `NOTICE` | Forscie / ITM attribution |
| `web/README.md` | UI local + Pages notes |
| `apps/aggregator/config.py` | Add RSS sources |
| `apps/aggregator/archive_sources.py` | Sitemap archive sources + keywords |
| `apps/aggregator/archive_pipeline.py` | `ingest_archive` → RawArticle |
| `apps/aggregator/courtlistener.py` | RECAP search → RawArticle |
| `apps/aggregator/web_keywords.py` | Alert RSS → RawArticle |
| `apps/aggregator/export.py` | One-way NDJSON export package |
| `shared/itm/` | ITM index load + refresh |
| `shared/data/itm_index.json` | Slim taxonomy |
| `shared/utils/entities.py` | ITM match + relevance score |
| `apps/search/api.py` | FastAPI routes + CORS |
| `web/` | Static reader + workbench |

---

## Design constraints (do not break)

1. Type hints + Pydantic at boundaries.
2. LangGraph for agent workflows; small explicit nodes.
3. No hardcoded secrets — env / `.env`.
4. Cheap by default — MVP works offline (committed `itm_index.json`).
5. Composition over inheritance.
6. Prefer small, reviewable changes.
7. Trademark-safe ITM references only (see `NOTICE`).

---

## Suggested next work

1. **Stand up Google Alerts** for overemployment / moonlighting phrases →
   `WEB_KEYWORD_FEED_URLS` (see [`docs/sourcing.md`](sourcing.md)), then
   `ingest_web_keywords` → `process --force` → `/reload`.
2. Reprocess after ITM/alias/feed changes: `process --force`, then `/reload`.
3. Wire corporate job to pull `export` NDJSON or `/export/articles`.
4. Expand `shared/itm/aliases.py` for noisy / missing OSINT phrases.
5. Promote / merge POC UI when Hunt/Matrix sync settles (`working-intel-v1`
   is the public demo save point at `https://intel.thederpweb.com`).
6. **Later — Hunt Package:** flag + multi-article export → markdown template → optional AI; then social/X or Feedly tip accounts.
7. Expand `archive_sources.py` (Krebs / Dark Reading archive indexes); Postgres + pgvector; LLM `ai_summary` / ITM classification.
8. **IF038 TTPs:** [`docs/ttps_overemployment.md`](ttps_overemployment.md) — deepen with CourtListener MCP + opinion text (LLM batch later).

---

## Gotchas

- Run from `insider-intel/` after `pip install -e .`.
- Empty UI: run `aggregator all`, then `POST /reload` if API already up.
- **Sources sidebar:** after ingest/process, always `POST /reload` so `/sources` merges configured feeds with newly indexed `source_id`s (Feedly boards, new RSS). After editing `DEFAULT_FEEDS` / feeds JSON, **restart the API** too — Python won't reload the config module otherwise.
- CORS: page origin must be in `CORS_ORIGINS` (default includes `:5500` and `null` for file://).
- De-dupe by `link`. `process --force` **upserts** (rewrites) processed JSONL so each link appears once.
- After `refresh_itm` or alias edits, always `process --force` so stored `itm_hits` update.

---

## Quick verification

```bash
cd insider-intel
ruff check apps shared tests
pytest -q
python -m apps.search query "insider" --limit 3
```
