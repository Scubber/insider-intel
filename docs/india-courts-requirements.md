# India court-judgment ingestion — requirements brief (start-from-scratch)

Operator-approved requirements for adding Indian court-judgment coverage, distilled from
the 2026-08-22 evaluation session (repo audit with file:line verification + source and
model research). Use as the starting prompt/spec for a fresh implementation session.

**Operator decisions locked in this brief:** extend this repo (no separate system);
**do NOT use Indian Kanoon** — no association with the site, no metered API; the lane is
built on the free CC BY 4.0 open dataset, processed on the DGX Spark ("sparky") at $0;
phased delivery with the spend-gate fix first.

---

## Task

You are working in the production repository `Scubber/insider-intel`. Merging to `main`
deploys. Read `CLAUDE.md`, then `docs/HANDOFF.md`, then `docs/dgx-spark.md` before
changing anything. Develop on your designated branch, commit with clear messages, push
when complete. Do **not** merge, deploy, or alter production credentials, and do not open
PRs unless asked. Build and test through the Makefile (`make test` / `make lint`).

## Objective

Add Indian litigation as a source of insider-threat behavioral evidence — a new
**ingestion lane inside this repo** feeding the existing score → classify → enrich
pipeline and the EVIDENCE product. Suggested identifiers:
`source_id = indiacourts-judgments` (prefix `indiacourts-*`),
`source_name = Indian High Court Judgments (eCourts open dataset)`,
`channel = filings`, country `IN`.

This is behavioral corpus research, never employee screening. Hard boundaries
(permanent): no employee-name searches, no nationality inference, no person/persona
graphs or cross-case entity resolution, no individual risk scores, roles never
individuals, alleged/reported/admitted/adjudicated never conflated, enrichment history
append-only, match-signal text in `RawArticle.content` never `summary`, secrets only via
`shared/settings.py`/env, no eCourts portal scraping or CAPTCHA automation, no LLM calls
at read time, **no Indian Kanoon integration** (operator exclusion: no association, no
metered spend).

## Data source (the whole lane is $0)

**Indian High Court Judgments open dataset** — AWS Open Data Registry, maintained by the
`vanga/indian-high-court-judgments` project (companion Supreme Court dataset on the same
registry). Facts verified 2026-08-22:

- 17.8M judgments, ~1.25 TiB of PDF tars, 25 High Courts. **CC BY 4.0** — storing full
  text permanently in GCS and re-serving it is clearly permitted; attribution is a simple
  credit line (dataset + eCourts as ultimate source). No logo, no terms negotiation.
- **Updated daily** via the maintainer's automated pipeline.
- Access is AWS-sponsored (free egress). Layout: `metadata/parquet/` (queryable
  structured metadata: `cnr`, `decision_date`, `order_number`, court/bench codes in
  `STATE~COURT` form, `pdf_exists`, `source`), `data/tar/` (PDF archives),
  `metadata/tar/` (raw JSON) — partitioned `year/court/bench`; selective partition
  download is the intended bulk interface. Cross-source identity:
  `(cnr, decision_date, order_number)`, falling back to `(cnr, decision_date)`.
- **Never download the whole corpus.** Parquet-first selection, then only the court/year
  tar partitions in scope.

Discovery model: there is no search service — sparky downloads targeted partitions,
extracts text, and runs the insider lexicon **locally** (plain code, the same behavior
clusters as the US queries: personal-email exfiltration, pen drive, source-code/customer
data theft, notice period, moonlighting, deletion/formatting, WhatsApp/personal cloud,
criminal breach of trust, IT Act §43/§66). Matching judgments become `RawArticle` rows;
non-matching text is discarded (only matches enter the corpus). Forward coverage = daily
parquet sync of new decision dates (~1-day lag); history = backward partition walk.

## Processing stack on sparky (researched 2026-08; all free, all local)

1. **Text layer first, no AI**: PyMuPDF extraction on CPU. Most recent HC judgments are
   born-digital; this covers the majority at ~ms/page. Detect empty/garbage text layers
   and route those PDFs to OCR.
2. **OCR fallback via vLLM on the GB10** (only for scanned PDFs):
   - Primary: **olmOCR-2-7B** (AllenAI, Oct 2025) — purpose-built bulk PDF→text pipeline
     with its own vLLM batch tool, 82.4% olmOCR-bench, fully open license.
   - Throughput alternative: **DeepSeek-OCR 2** (Jan 2026, MIT, vLLM-supported) — fastest
     per page.
   - Indic-script fallback: **Surya** (650M, best small model at 83.3% olmOCR-bench,
     strongest Indic support; Datalab license is free below a revenue threshold — fine
     here). English-language judgments dominate HC output; regional-language judgments
     may be deferred as a documented coverage gap rather than blocking the lane.
   - PaddleOCR-VL only if the above miss — Paddle on ARM64/GB10 is a compatibility risk.
3. **Enrichment**: the existing sparky chain — currently **Nemotron** served by vLLM
   (`model: auto`; do not pin a SKU in this repo). Notes for the operator: Nemotron 3
   Nano (30B-A3B MoE) has an official DGX Spark playbook and suits the GB10's
   memory-bandwidth-bound throughput; Nemotron 3 Super (120B-A12B, open-weight) fits at
   FP4 for higher extraction quality; Nemotron 3 is trained for JSON-schema structured
   outputs — enabling vLLM guided decoding should resolve the known local-model JSON
   parse failures noted in HANDOFF. `docs/dgx-spark.md` still names Qwen and needs a
   doc refresh (separate concern; note it, don't block on it).
4. This lane's heavy work (download, extract, OCR, scan) runs **on the Spark tenant**,
   bounded so it never starves the 4×-daily refresh cron: per-run caps on partitions
   downloaded, PDFs extracted, and OCR pages; resumable state under `data/state/`.
   **Disk is a rolling buffer, never cumulative**: download a tar partition → extract
   text → scan → keep only matching judgments → delete the tar before the next
   partition. Working space is a few GB at a time regardless of total corpus size.

## Deliver in three phases, in order, as separate PR-sized chunks

### Phase 1 — spend-gate integrity (before the lane; this is HANDOFF item 10, the ACTIVE work item)

Still required even with $0 local enrichment: the gates keep non-insider litigation out
of the corpus and out of paid-chain fallback runs. Verified state as of 2026-08-22:

- The enrichment spend policy, prompt-size cap, and head+tail packing all derive channel
  from `resolve_channel(source_id)` (`shared/schemas/articles.py:41`), which returns
  `"filings"` only when `"courtlistener"` is in the id. `RawArticle.channel` is ignored
  by the gate (`shared/agents/summarize.py` ~:340, :156, :352; `shared/llm/base.py`
  ~:403). Existing `canlii-*` filings rows bill under the NEWS gate today; any
  `indiacourts-*` row would too.
- The filings gate (`summarize.py::qualifies`, ~:110-128) requires body ≥ 1500 chars plus
  `_body_has_itm_signal` = **any one ITM alias** — too weak (58% of post-gate enrichments
  adjudicated non-insider per the 2026-08-04 audit). `INSIDER_FRAMING_KEYWORDS`
  (`shared/itm/aliases.py:273`) never reaches the gate.
- The gate scans `clean_text = title + summary + content`, and lanes store their match
  marker at the head of `content` — a marker phrased in insider terms can self-satisfy
  the signal check.

Work: (1) make the gate honor filings for non-CourtListener court sources (stored
channel or a filings source-prefix set including `canlii-*` and `indiacourts-*`) across
`qualifies()`, cap selection, and head+tail packing; (2) require an
`INSIDER_FRAMING_KEYWORDS` hit in the body per HANDOFF's proposed fix; (3) strip a
source's own match-marker line before the in-body signal check; (4) write a dry-run
replay script (per HANDOFF: ~937 stored non-insider vs ~540 insider filings rows) for
the operator to run on sparky, treated as a review gate; (5) extend
`tests/test_channel.py` and the gate tests. State the CanLII blast radius in the PR body
(operator has blessed the direction).

### Phase 2 — the dataset lane (dark: disabled by default, fixture-tested)

Follow the CourtListener two-module precedent (`apps/aggregator/courtlistener.py` +
`courtlistener_pipeline.py`) in spirit: a source module + a pipeline module + CLI
commands, but the mechanics are dataset-shaped, not API-shaped:

- `apps/aggregator/indiacourts.py`: dataset client — parquet metadata sync for target
  courts/years, tar partition fetch, PDF text-layer extraction, OCR routing, judgment
  text normalization (preserve paragraph structure), the local insider-lexicon scanner
  (hand-authored compound patterns; no broad standalone terms like "employee" or
  "fraud"), and the hit→`RawArticle` mapper. The matched-pattern marker goes in
  `content` (never `summary`); `summary` carries human-visible court/case metadata and
  MUST emit literal `"Court: …"` / `"Docket: …"` lines (use CNR/case number) — story
  clustering parses exactly those from summary text (`shared/utils/story_key.py:63-90`).
- `apps/aggregator/indiacourts_pipeline.py`: three jobs —
  `ingest_indiacourts` (daily forward sync: new decision-date rows for target courts →
  fetch PDFs → extract → scan → store matches),
  `sweep_indiacourts_history` (backward walk over year/court partitions with a cursor in
  `data/state/ingest_state.json`; the cursor advances only when a partition completed;
  resumable), and
  `extract_indiacourts_pending` (bounded re-extraction/OCR queue for PDFs that failed
  text extraction, with attempt markers and a retry interval — mirror the CourtListener
  backfill contract: content-append, `refresh(force=True)` for a fresh `ingested_at`,
  and a `_clear_llm_fields`-equivalent that **never clears `enrichment_history`**;
  skipping the clear step pins a thin enrichment forever via the carry-forward cache-hit
  at `shared/agents/article_processor.py:211-235`).
- Bounding: per-run caps on partitions, PDFs, OCR pages; all knobs as `INDIACOURTS_*`
  settings in `shared/settings.py` (disabled by default; no configured courts → no
  network); CLI wiring per the `__main__.py` three-part convention; `run_all.py`
  integration behind `INDIACOURTS_ENABLED`; `expected_lane_specs` branch gated by the
  enable setting (clean absence when disabled — `social_ingest_enabled` precedent at
  `apps/aggregator/lane_health.py:116`) + `_infer_kind` prefix; `.env.example` +
  `.env.spark.example`; `docs/india-courts-ingest.md`; CLAUDE.md + HANDOFF updates in
  the same PR.
- Quick wins (operator-approved, config-only): verify whether Indian
  regulator/prosecutor feeds exist (CBI, ED, SEBI, CERT-In) and add working ones to
  `config.py` per the phase-0 prosecutor-feed pattern; add India-scoped queries to the
  `web_keywords` lane.
- Tests in `tests/test_indiacourts.py` (fixtures only; no copyrighted full judgments; no
  live network): parquet row → candidate selection, court/year partition targeting,
  text-layer vs OCR routing decision, extraction normalization, lexicon scanner
  (positive/negative/compound patterns), marker in `content` not `summary`,
  `Court:`/`Docket:` summary lines, dedupe by `(cnr, decision_date, order_number)` with
  fallback, cursor/resume semantics, per-run caps, retry-interval markers, forced
  reprocess reaching the enrichment gate, lane-health states (disabled → absent;
  enabled-but-empty → healthy; extraction failures tracked).

### Phase 3 — provenance, posture, and surfaces

- Optional `LegalMetadata` object (country_code, jurisdiction, court_name, court_level,
  document_kind, procedural_stage, case_number, **cnr**, source_document_id,
  decision_date, language, source_terms) on `RawArticle` and `ProcessedArticle`. It does
  **not** flow automatically — thread it explicitly at: the mapper, `_node_assemble`
  (`article_processor.py:325-348`), `SearchHit` + `apps/search/index.py::_to_hit`, the
  ledger row projection (`apps/search/service.py:175-184`), and the pure-stdlib Actions
  path (`scripts/evidence_ledger.py`). Back-compat is safe (pydantic defaults; JSONL
  loaders skip bad lines). Authored `source_id → country` fallback resolver
  (`courtlistener-*`→US, `canlii-*`→CA, `indiacourts-*`→IN); explicit metadata wins.
  The dataset supplies real CNRs — never fabricate one where absent.
- Indian legal postures: `legal_posture` is enforced in **three unlinked places** —
  `shared/schemas/forensics.py:84-94` (+ coercion at :519/:524, unknown→"unknown"), the
  prompt enum in `shared/llm/base.py:295`, and `POSTURE_WEIGHT` in
  `shared/utils/evidence.py:47-56`. Critical: a posture missing from `POSTURE_WEIGHT`
  leaves claim_status **uncapped** in `case_strength` (evidence.py:344-358) — an
  FIR/bail/interim-injunction document whose methods the LLM stamped "adjudicated" would
  count court-proven. Add every new stage (fir_allegation, charge_sheet,
  interim_injunction, bail, quashing, writ_review, disciplinary_proceeding,
  arbitral_proceeding, civil_decree, trial_judgment…) at **all three sites** with weight
  below `POSTURE_ADJUDICATED_MIN_WEIGHT` (4) unless genuinely adjudicative, plus a
  cross-site drift-tripwire test (none exists). Update the enrichment prompt: recited
  allegations ≠ adjudicated; bail/quashing/interim relief ≠ conviction; disciplinary
  finding ≠ criminal conviction; `admitted` needs an admission/plea; ambiguity stays
  `unclear`. (Indian judgments are often forensically rich at pre-adjudication stages —
  e.g. bail orders reciting exact email accounts and dates — so this separation is what
  makes the richness safe to use.)
- `country_code` facet: four layers (predicate in `apps/search/index.py` alongside
  `_article_matches_channel`, pass-through in `service.py`, Query params in `api.py` on
  `/articles`/`/search`/`/sources`, UI wiring) plus `/feed.xml` parity.
  `GET /export/articles` (NDJSON, token-gated) filters only min_score/since/
  itm_alignment today — a country filter is new work; mind `EXPORT_SCHEMA_VERSION`
  (`insider-intel.export.v5`, test-pinned).
- UI: IN jurisdiction chip in `buildArticleRow`'s metaParts idiom with a `data-tip`;
  "every page teaches itself" applies (purpose line, tooltips, empty states;
  `tests/test_site_guide.py` fails bare tabs). **Attribution is one credit line** (the
  CC BY 4.0 dataset + eCourts as ultimate source) on the ABOUT pane — extend
  `tests/test_about_page.py` (minimal-pane, attribution-lines, no-corpus-digits
  contracts). `ui-smoke` must pass at 390/1280; Playwright evidence in the PR body.
  `web/findings.json` needs no rewrite (static, publish-by-merge).
- **EVIDENCE separates by country (operator requirement 2026-08-22)** — a filter, not
  just a payload breakdown:
  - API: `country` query param on `GET /evidence/ledger` AND
    `GET /evidence/technique/{id}` — the ledger recomputes over the country-sliced row
    set (pure-stdlib `build_evidence_ledger` unchanged in spirit; country threaded into
    the row projection at `apps/search/service.py:175-184` and the Actions-runner path).
    No param = global ledger, exactly today's behavior. Countries are enumerated from
    the data (via legal_metadata / the source-prefix resolver), never a hardcoded list.
  - UI: a jurisdiction switch on the EVIDENCE pane (ALL | per-country chips rendered
    only for countries present in the corpus), carried into the per-technique dossier
    tie-ins. Per "every page teaches itself": a `data-tip` stating jurisdiction = the
    court system of the source records, never actor nationality.
  - The basis line names the active slice (e.g. `BASED ON <N> VERDICT-TRUE CASES ·
    JURISDICTION: IN · AS OF <date>Z`). `SMALL_N_FLOOR=10` applies per-slice — sliced
    views will suppress percentages more often, which is correct; the suppressed/empty
    state says why and how it fills (more adjudicated cases in that jurisdiction).
  - Limitations copy: different jurisdictions contribute different document types and
    procedural stages, so rates are NOT comparable across country slices — the filter
    presents per-jurisdiction views, never cross-country comparisons of people;
    coverage equals the lexicon (no semantic discovery at the scan stage).
  - `web/findings.json` stays global and untouched; the boot snapshot may carry only
    the global slice (country slices can require the live API — CACHED view shows ALL).
  - TOOLING may inherit the same param later; out of scope for this change unless
    trivial. Contract tests in the style of `tests/test_matrix_data_sources.py` pin the
    param, the per-slice floor behavior, and byte-identical global output when the
    param is absent.

## Coverage scope (operator-decided 2026-08-22 — not open for re-scoping)

- **Courts: all 25 High Courts.** No exclusions. Rationale: the rolling-buffer design
  makes a court's cost pure background compute, and insider cases arise outside obvious
  tech hubs — the dataset's largest court by volume is Punjab & Haryana (1.84M
  judgments, the Gurgaon tech-belt court behind the Abhinav Gupta email-exfiltration
  case). Scoping by court loses cases and saves nothing that matters.
- **History floor: 2000-01-01** (`INDIACOURTS_HISTORY_FLOOR` default). Rationale: the
  IT Act 2000 opens India's cybercrime-statute era and the IT/BPO employment boom is a
  2000s phenomenon; per the dataset's STATS.md, meaningful volume starts ~2004 anyway
  (~50k PDFs in 2004 → ~405k in 2010 → 1.79M peak in 2023; pre-2000 is negligible), so
  the 2000 floor is effectively "everything" at no extra cost. Note: unlike the US
  lane's 2015 CourtListener floor, there is no API throttle here — depth is free.
- **Walk order: newest-first, hub-courts-first** (Delhi, Bombay, Karnataka, Madras,
  Telangana, Punjab & Haryana lead; remaining courts follow). Ordering controls how
  fast insider signal reaches EVIDENCE, not coverage — every court/year in scope gets
  processed eventually. Expect the pre-2010 tail to skew scanned (higher OCR share,
  slower); volumes there are small, so it is acceptable background work.
- Deep history is a product feature: tactic evolution over time (floppy → pen drive →
  personal email → WhatsApp) charts on the EVIDENCE year axis.

## Operator pre-flight (don't block coding; record gaps in the final report)

1. Confirm free disk on sparky covers the rolling working buffer plus the retained
   matched-judgment text (small; the tars are deleted after extraction).
2. Confirm the OCR model choice after a bench run on sparky (olmOCR-2-7B default;
   DeepSeek-OCR 2 for speed; Surya for Indic scripts) and that vLLM serves it alongside
   or time-shared with the enrichment model within 128GB.
3. Phase 1 dry-run execution on sparky (script provided by the implementation).
4. PR mechanics: whether the implementing session may open draft PRs for CI feedback.

## Definition of done

Phases 1–3 implemented and pushed to the designated branch; lane disabled by default;
zero external spend and no Indian Kanoon anywhere; all existing lanes still pass;
`make test` + `make lint` green; fixtures only (no live-network claims); docs updated in
the same PRs; no secrets committed; no merge/deploy. Final report: architecture, files
changed, settings added, exact test results, OCR/extraction behavior left unverified on
real partitions, activation steps (enable on sparky + scope settings), limitations, and
any deviations from this brief with reasons.
