# Roadmap

Where insider-intel is and where it's going. The mission: identify **how
people actually commit insider threat activity**, from primary sources,
mapped to the Insider Threat Matrix™.

## Shipped (production)

**Foundation**
- Multi-lane ingestion: RSS/legal/HR press, CourtListener (RECAP + opinions),
  Feedly, DataTheftNews, web-keyword RSS, Reddit/X social subscriptions
- LangGraph processing: ITM technique matching, scoring, use-case +
  insider-type classification, embeddings, story clustering
- Dossier UI: case-file stream, observed-only ITM rail with case filtering,
  full matrix browser, technique dossiers, hunt query packs, extraction board
- CD: merge-to-main deploys (keyless OIDC), GitHub-dispatchable corpus
  refresh (`refresh-corpus` workflow), all ops auditable in Actions

**Intelligence layer**
- LLM case records at ingest (`SUMMARIZER_LLM_PROVIDER`): analyst summary +
  structured facts (actor, access, methods, exfil, detection, outcome) +
  LLM-adjudicated ITM technique mapping; billed once per article, ever
- Full court-document harvesting from the free RECAP archive (paced to
  CourtListener's account-wide 10/min limit, throttle-safe)
- Budget-capped PACER purchasing via RECAP Fetch (≤ $27/quarter — under the
  fee waiver; every purchase enriches the public archive)
- Rolling historical sweep: 90-day windows of insider prosecutions walking
  back to 2015 (~10 days to seed the decade at 4 refreshes/day)
- Evidence-gated workbench extraction (no more unconditional seed packs);
  hunt report v2: analyst summary + per-technique sections with per-case
  "how they did it" bullets, LLM-enriched via `EXTRACT_LLM_PROVIDER`
  (xAI/Anthropic/OpenAI-compatible), honest evidence-pack labeling
- Read-the-filing: full court-document text on demand in the case card
  (`GET /articles/text`)
- Free-tier-sized X lane (consumer-key bearer minting, 48h cadence)

## In flight

- Historical decade seeding (automatic, ~10 days from 2026-07-18)
- Summarizer verification in prod (`Case record extracted` log lines /
  CASE RECORD blocks on the site)
- First PACER purchase → first Free Law Project contribution

## Next up

Tracked as GitHub issues (the working kanban):
<https://github.com/Scubber/insider-intel/issues>

- **Syndication — let other sites/tools consume insider-intel** (see below)
- Reddit OAuth creds → un-block the social tips lane from GCP
- Behavior→telemetry hunt terms with provenance (workbench track 2)
- Novel-TTP discovery loop: residual embedding vs ITM descriptions →
  alias candidates + novelty queue (workbench track 3)
- Feed-list hygiene pass (dead/blocked RSS sources)
- GitOps job configuration: declare corpus-refresh env/secrets mappings in
  deploy-api.yml so every deploy re-asserts config from the repo

## Syndication design (proposed)

Tiered by consumer type; each tier builds on the last:

1. **Feeds (baseline, cheap)** — `GET /feed.xml` (RSS 2.0), `/feed.atom`,
   and `/feed.json` (JSON Feed 1.1) rendered from the index, cached, with
   facet variants (`?use_case=`, `?channel=filings`, `?itm_id=`). Items link
   to the site; case records land in `content:encoded` / JSON `_insider`
   extension. Every aggregator on earth consumes this.
2. **Security-native (the differentiator)** — export case records as a
   **MISP feed** (static JSON manifest the MISP ecosystem can subscribe to)
   and/or **STIX 2.1 bundles** at `GET /export/stix` — SOCs pull structured
   insider TTPs straight into their TIP/SIEM with ITM technique references
   intact. This is what makes insider-intel *machine-consumable threat
   intel* rather than a news site.
3. **Push/social (later)** — WebSub pings on feed updates; optionally an
   ActivityPub actor so the Fediverse security community can follow cases
   natively.

The REST API (`/articles`, `/search`, CORS-open) is already the power-user
option and gets documented as such alongside tier 1.

## Later / parked

- Postgres + pgvector; archive-source expansion
- Workbench full-screen tab
- PCL (PACER Case Locator) discovery lane if CourtListener index lag ever
  becomes a problem
- FLP outreach: rate-limit headroom (draft email exists)
