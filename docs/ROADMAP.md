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
- **Ingest-time forensic enrichment** (one LLM call per case → analyst note +
  `PerCaseForensics`); the hunt report assembles stored records in code, no
  read-time LLM
- **Evidence rigor** on the forensic record: methods carry `claim_status`
  (alleged / admitted / adjudicated / reported) + a source `evidence_quote`;
  observables carry a `basis` (mechanically_implied vs analyst_inference); each
  case carries `source_type` + `legal_posture`. The prompt forbids laundering
  allegations into findings and forbids inventing defender telemetry (vendor
  names, log sources, index/sourcetype/event-id/field names). UI shows a
  posture badge and marks inferred observables. These are the inputs the
  discovery pass (below) gates promotion on.

## In flight

- Historical decade seeding (automatic, ~10 days from 2026-07-18)
- Summarizer verification in prod (`Case record extracted` log lines /
  CASE RECORD blocks on the site)
- First PACER purchase → first Free Law Project contribution

## Next up

Tracked as GitHub issues (the working kanban):
<https://github.com/Scubber/insider-intel/issues>

- **Novel-technique discovery pass** — the north star; see below
- **Syndication — let other sites/tools consume insider-intel** (see below)
- Reddit OAuth creds → un-block the social tips lane from GCP
- Behavior→telemetry hunt terms with provenance (workbench track 2)
- Feed-list hygiene pass (dead/blocked RSS sources)
- GitOps job configuration: declare corpus-refresh env/secrets mappings in
  deploy-api.yml so every deploy re-asserts config from the repo

## Novel-technique discovery pass (proposed — the product's north star)

The goal of the product is to identify, tag, and **discover** insider &
forensic techniques — including novel ones not yet in any catalog. ITM mapping
is the reference substrate we diff against to tell known behavior from novel
behavior; discovery is the point.

A second synthesis pass consumes the **extraction JSON** (never the raw
filing — the point is to reason over already-vetted facts), maps each method
against the ITM, and for behavior the ITM doesn't cover proposes a **seed
candidate** with a promotion lifecycle:

- 1 detailed case → **seed**
- 2 independent cases, same portable behavior → **corroborated**
- corroborated + clearly distinct from every ITM technique → **eligible** for a
  new technique id
- same behavior, different tool → a **procedure / variant**, not a new technique

It separates `portable_behavior` (reusable across cases) from
`case_specific_procedure`, and gates promotion on the evidence-rigor fields
this repo now stores:

- never mint from an `analyst_inference`-only observable (needs a
  mechanically-implied trace or a source-stated fact)
- a strong seed needs `claim_status` above `alleged` (admitted / adjudicated),
  or independent corroboration
- `legal_posture` + `evidence_quote` travel with the candidate for human review

Trade-offs to plan for: a second LLM call per case (ingest cost), a human
review / dedup workflow before an id is permanent, and storage for seed
candidates alongside the ITM. Tier 1+2 (the evidence foundation) shipped first
precisely so this pass consumes trustworthy input instead of laundered
allegations and hallucinated telemetry.

### Follow-up: retroactive re-enrichment of the stuck band (sequenced last)

The evidence-rigor fields are **not retroactive**. New ingests get them, and
the backfill sweep re-bills legacy `case_record`-only rows with the new prompt
— but articles already enriched to full `forensics` under the old prompt
cache-hit and are never re-billed (`_node_summarize` reuses `prior_forensics`;
the upgrade sweep only runs when `prior_forensics is None`). That band (cases
enriched between the #36 pivot and the Tier 1+2 ship) keeps the weak defaults
— `claim_status="unclear"`, observable `basis="analyst_inference"`,
`source_type`/`legal_posture="unknown"` — so the discovery pass would *skip*
them at the promotion gates rather than mint anything wrong.

Fix (mirrors the existing legacy-upgrade pattern): stamp a prompt/schema
version on `forensics` and have the refresh sweep re-bill records below the
current version, bounded by the same per-run cap — one re-bill per stale
record, gradual and cheap.

**Sequencing: do this LAST — only after the flagging quality (Tier 1+2) and
the discovery logic are validated in production.** Re-billing the corpus before
the prompt and gates are proven would just pay to bake in a standard we're
still tuning.

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
