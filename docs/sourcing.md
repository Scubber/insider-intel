# Sourcing: multi-domain insider risk

Insider risk spans **HR, legal, business risk, infosec, and enterprise risk**.
This product is not an infosec-only Feedly clone. **News budget for paid
sources: under $100/year total (prefer $0).** Law360-class seats are out of
scope; Feedly is optional inspiration only.

Primary free stack:

1. **Google Alerts RSS** → `WEB_KEYWORD_FEED_URLS` (cross-domain discovery)
2. **CourtListener** → filings / employment-adjacent dockets
3. **Multi-domain RSS** — HR Dive, employment-law blogs, plus existing infosec feeds
4. **Sitemap archive** → `ingest_archive` (keyword-filtered history; RSS alone has no backfill)

See also: Hunt term → ITM tags → articles (`docs/architecture.md` §4).

## New Source checklist (required)

Use the **same `source_id`** across lanes so Sources stay unified. Mark N/A with a
one-line reason in the coverage table below.

| Step | Lane | Action | Command / wire-up |
|------|------|--------|-------------------|
| 1 | **RSS** | Find a working feed; smoke-fetch; add to `DEFAULT_FEEDS` / feeds JSON | `python -m apps.aggregator ingest` |
| 2 | **Archive** | Check `robots.txt` / sitemap / news-archive index; add to `archive_sources.py` | `python -m apps.aggregator ingest_archive` |
| 3 | **Google Alerts** | If no/weak RSS or cross-site beat (BI, SHRM, NLR) | `WEB_KEYWORD_FEED_URLS` → `ingest_web_keywords` |
| 4 | **CourtListener** | If legal/filings beat — add RECAP docket / opinion queries (not a site scrape) | `ingest_courtlistener` |
| 5 | **Tips** | Tip/social only → `channel=tips` | Reddit / tips feeds |
| 6 | **Hunt aliases** | New operator phrases → `shared/itm/aliases.py` | Maps-to ITM |
| 7 | **Smoke** | Ingest wired lanes → `process` → `POST /reload` | Confirm Source in UI |

### Source coverage

| Source | RSS | Sitemap archive | Alerts | Filings | Tips |
|--------|-----|-----------------|--------|---------|------|
| HR Dive | yes | yes (`hrdive`) | N/A (has RSS) | N/A | N/A |
| Proskauer L&E | yes | yes (`proskauer-workplace`) | N/A | N/A | N/A |
| Infosec pillar (Krebs, DR, …) | yes | not yet | optional | N/A | N/A |
| CourtListener RECAP + opinions | N/A | N/A | N/A | yes | N/A |
| Google Alerts | via alert RSS | N/A | yes | N/A | N/A |
| Reddit tips | yes | N/A | N/A | N/A | yes |

## Wired free RSS (in repo)

| Source | URL | Notes |
|--------|-----|--------|
| HR Dive | `https://www.hrdive.com/feeds/news/` | HR / workplace (`DEFAULT_FEEDS`) |
| Proskauer Law and the Workplace | `https://www.lawandtheworkplace.com/feed/` | Employment law (`DEFAULT_FEEDS`) |
| Infosec pillar | Krebs, Dark Reading, … | Existing `DEFAULT_FEEDS` |

```bash
python -m apps.aggregator ingest --feeds-file apps/aggregator/feeds.multi_domain.example.json -v
```

## Sitemap archive (historical backfill)

RSS only carries a short rolling window (~10–50 items). Archives use public
sitemaps, keyword-filtered (IF038-class by default), then HTML metadata →
`RawArticle`.

```bash
# Both configured archive sources (HR Dive + Proskauer), up to 200 pages each
python -m apps.aggregator ingest_archive -v --max-urls 50 --delay 0.5

# One source / custom keywords
python -m apps.aggregator ingest_archive --source hrdive --keyword moonlighting --keyword overemployment -v
python -m apps.aggregator process
curl -X POST http://127.0.0.1:8000/reload
```

Config: `apps/aggregator/archive_sources.py`.

## Google Alerts (do this for overemployment / IF038)

Business Insider, National Law Review, and SHRM do **not** offer reliable public
RSS. Alerts catch their pages without paid seats.

1. Create Google Alerts for phrases such as:
   - `overemployment`
   - `overemployed`
   - `moonlighting employee`
   - `"concurrent employment"`
   - `"dual employment"`
   - `"outside employment" policy`
   - `"conflict of interest" employee remote`
2. For each alert: **Deliver to → RSS feed** → copy the feed URL.
3. Put URLs in `.env` (comma-separated), never commit them:

```env
WEB_KEYWORD_FEED_URLS=https://www.google.com/alerts/feeds/...,https://www.google.com/alerts/feeds/...
```

4. Ingest and refresh:

```bash
python -m apps.aggregator ingest_web_keywords -v
python -m apps.aggregator process --force
curl -X POST http://127.0.0.1:8000/reload
```

## CourtListener (filings)

Two search types share the v4 Search API, one `RawArticle` mapper each:

- **`dockets`** (`type=r`, source `courtlistener-recap`) — federal RECAP docket
  metadata (court, cause, parties). Default.
- **`opinions`** (`type=o`, source `courtlistener-opinions`) — written case law
  opinions; the search snippet (fact pattern text) lands in `summary`, so the
  ITM processor maps techniques from actual opinion language.

Oral-argument audio (`type=oa`) was considered and skipped: mostly appellate
procedural talk with thin ITM signal; the same spec table in
`apps/aggregator/courtlistener.py` makes it a small add later if needed.

Defaults include employment / moonlighting-style queries in
`apps/aggregator/courtlistener.py`. For deeper history, raise pages:

```bash
python -m apps.aggregator ingest_courtlistener --max-pages 3 -v
python -m apps.aggregator ingest_courtlistener --type opinions -v
python -m apps.aggregator ingest_courtlistener --type all -v
python -m apps.aggregator process --force
curl -X POST http://127.0.0.1:8000/reload
```

Override queries and types:

```env
COURTLISTENER_QUERIES="insider trading",moonlighting employee,"concurrent employment"
# pull opinions alongside dockets in `all` runs (default: dockets only)
COURTLISTENER_TYPES=all
# opinion-specific queries (empty = fall back to COURTLISTENER_QUERIES)
COURTLISTENER_OPINION_QUERIES="trade secret" former employee,"economic espionage"
```

### CourtListener MCP (agentic research)

Free Law Project also exposes a **Model Context Protocol** server for live legal
research (case law, RECAP/PACER metadata, citations, alerts, etc.) — separate
from our batch `ingest_courtlistener` → `RawArticle` path:

- Docs: [CourtListener MCP for agentic access](https://wiki.free.law/c/courtlistener/help/api/mcp/model-context-protocol-mcp-server-for-agentic-access)
- Server URL: `https://mcp.courtlistener.com` (OAuth)
- **Sign up:** [Create a CourtListener account](https://www.courtlistener.com/register/) (free; API access is granted to all accounts)
- **Cursor:** entry `courtlistener` is in `~/.cursor/mcp.json`. After signup:
  1. Cursor **Settings → Tools & MCP**
  2. Find **courtlistener** → **Connect** / authenticate
  3. Log in with your CourtListener account in the browser OAuth flow
- Use MCP for ad-hoc docket/opinion research; **do not** treat MCP hits as a
  substitute for the ingest pipeline unless you explicitly map results into
  `RawArticle`.

Elevated API/MCP usage may require Free Law Project membership; check
[API usage](https://www.courtlistener.com/profile/api-usage/).

## Full pipeline

```bash
python -m apps.aggregator all
# restart API if DEFAULT_FEEDS changed, then:
curl -X POST http://127.0.0.1:8000/reload
```

After ingest, Hunt **Overemployment** maps to **IF038**; archive / Alerts /
filings / HR RSS fill topical copy into the index.

Investigator TTP cheat sheet (email / chat / network / human — not SOC-only):
[`docs/ttps_overemployment.md`](ttps_overemployment.md).
