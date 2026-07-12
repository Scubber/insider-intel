# insider-intel

**Multi-domain insider-risk** OSINT aggregator (HR / legal / business / infosec)
aligned to the [Insider Threat Matrix™](https://insiderthreatmatrix.org/) for
minting hunt / detection keywords. Not an infosec-only Feedly clone — sourcing
runbook: [`docs/sourcing.md`](docs/sourcing.md).

Public UI: **https://intel.thederpweb.com** (alias **https://td3.dev**).
Repo: **https://github.com/Scubber/insider-intel**. Brand hub: thederpweb.com.

Insider Threat Matrix™ is owned by Forscie Limited. See [`NOTICE`](NOTICE).

## For Cursor / AI agents

**Start here:** [`docs/CURSOR_HANDOFF.md`](docs/CURSOR_HANDOFF.md) — full handoff
(status, commands, file map, next steps).

## MVP status

| Layer | Status |
|-------|--------|
| RSS ingestion (easy add sources) | ✅ |
| CourtListener RECAP + SEC/DOJ legal feeds | ✅ |
| Multi-domain RSS (HR Dive, employment-law blogs) | ✅ |
| Sitemap archive backfill (`ingest_archive`) | ✅ |
| Google Alerts / web-keyword RSS (when configured) | ✅ |
| Feedly boards (optional) | ✅ |
| LangGraph processing (ITM technique match + score) | ✅ |
| Slim ITM taxonomy (`shared/data/itm_index.json`) | ✅ |
| Local storage (JSONL) | ✅ |
| Article stream + sources + ITM filters + search API | ✅ |
| Static UI (`web/`) — Stream \| Matrix + ITM chips + operator-term workbench | ✅ |
| One-way corporate export (CLI NDJSON + `GET /export/articles`) | ✅ |
| Web scrapers (non-RSS) | ✅ sitemap archive MVP (`ingest_archive`) |
| Postgres + pgvector | 🔜 |
| LLM summaries | 🔜 (`ai_summary` reserved) |
| Cloudflare Pages + Cloud Run | 🟡 | Pages UI live; Cloud Run API via `scripts/deploy_cloud_run.sh` |

## Quick start (local)

```bash
# Install (Python 3.12+)
pip install -e ".[dev]"

# Full pipeline: fetch feeds → process → embed
python -m apps.aggregator all

# API
python -m apps.search serve
# → http://127.0.0.1:8000/docs

# UI (separate terminal)
python -m http.server 5500 --directory web
# → http://127.0.0.1:5500
```

### Commands

```bash
python -m apps.aggregator ingest --feeds-file apps/aggregator/feeds.example.json -v
python -m apps.aggregator ingest_feedly   # requires FEEDLY_* env
python -m apps.aggregator ingest_courtlistener
python -m apps.aggregator ingest_web_keywords  # requires WEB_KEYWORD_FEED_URLS
python -m apps.aggregator process --force
python -m apps.aggregator export --out dist/export
python -m apps.aggregator refresh_itm   # refresh slim ITM index from Forscie JSON
python -m apps.search query "exfiltration" --mode hybrid
```

After lexicon / ITM / feed changes: `process --force`, then `POST /reload` if the
API is already running. Default `PROCESS_MIN_SCORE=0.15` (UI stream matches).

### Corporate integration (one-way out)

Corporate tools **pull** OSINT; this repo never reads Graph/Teams/email/SIEM.

```bash
python -m apps.aggregator export --out dist/export
# → dist/export/articles.ndjson + manifest.json (schema insider-intel.export.v1)
```

Or set `EXPORT_API_TOKEN` and `GET /export/articles` with `Authorization: Bearer …`.

### Launch UI + API together

```bash
python scripts/launch_local.py
# → API :8000 + UI :5500, opens in Cursor Simple Browser
# python scripts/launch_local.py --browser   # OS default browser instead
```

A Cursor project hook also auto-runs this after agent turns that edit `insider-intel/`.
Or run task **insider-intel: open Simple Browser** from the Command Palette.

## Architecture

```
RSS feeds
   → ingest → data/raw/articles.jsonl
   → LangGraph (ITM match + score) → data/processed/articles.jsonl
   → FastAPI /articles /itm /sources /search
   → web/ (Stream | Matrix tabs + ITM filters + operator / technique handoff)
```

Articles are tagged with Insider Threat Matrix™ technique IDs across Motive,
Means, Preparation, Infringement, and Anti-Forensics. The keyword workbench
exports **operator search terms** (plaintext / JSON / LLM prompt) for Teams,
email, and SIEM paste; ITM chips remain taxonomy and filter context — not the
primary clipboard payload. The reader defaults to **ITM-aligned insider
scenarios only** (Motive / Means / Preparation / Infringement / Anti-Forensics),
with header chips to switch **ITM-aligned** vs **All indexed**. Use the
**Matrix** tab for a five-column technique browser (search → DT/PV handoff →
related articles). No SIEM dialect builders (KQL/SPL) in MVP.

## Adding sources

Edit `apps/aggregator/config.py` or pass JSON like
`apps/aggregator/feeds.example.json` /
`apps/aggregator/feeds.insider_board.example.json`.

### Feedly boards (Insider Threats x Top Stories / ITM-Hunt)

1. Create a Feedly developer token (Teams / Pro).
2. Copy each board’s **streamId** from Feedly.
3. Put them in `.env`:

```bash
FEEDLY_ACCESS_TOKEN=your_token
FEEDLY_STREAM_IDS=user/.../tag/...,enterprise/.../tag/...
```

4. Pull:

```bash
python -m apps.aggregator ingest_feedly
# or as part of the full pipeline:
python -m apps.aggregator all
```

Feedly labels (board names, keywords) are appended into the article summary so
ITM matching and search can use them. Without a token, Feedly ingest is skipped
and RSS sources still run.

## Configuration

Copy `.env.example` → `.env`. For hosted UI later, set `CORS_ORIGINS` and
`web/config.js` API base URL. See [docs/hosting.md](docs/hosting.md).

**Public UI:** `https://intel.thederpweb.com` (GitHub Pages).  
**Public API:** `https://api.intel.thederpweb.com` (Cloud Run — see [docs/hosting.md](docs/hosting.md)).  
Offline fallback snapshot: `?demo=1` (`web/demo/`; regenerate with
`python scripts/export_demo_snapshot.py`).

## Tests

```bash
pytest
ruff check apps shared tests
```

## Design notes

- **Product:** ITM-aligned insider OSINT → hunt/detection keywords (not generic cyber Feedly).
- **Cheap by default:** no paid APIs required for MVP.
- **Embeddings:** local hashing embedder — swap later.
- **Storage:** JSONL MVP; `DATABASE_URL` reserved for Postgres/pgvector.
- **Scrapers:** sitemap keyword archive (`ingest_archive`) for HR Dive /
  Proskauer; expand `archive_sources.py` for more publishers.
- **Hosting:** local-first; `web/` is Pages-ready; FastAPI stays a separate service.
