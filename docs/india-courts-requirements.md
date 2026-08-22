# India court-judgment ingestion — requirements brief (start-from-scratch)

This is the operator-approved requirements brief for adding Indian court-judgment
coverage, distilled from the 2026-08-22 evaluation session (repo audit with file:line
verification + Indian Kanoon API research). Use it as the starting prompt/spec for a
fresh implementation session. It supersedes the earlier externally-authored brief.

---

## Task

You are working in the production repository `Scubber/insider-intel`. Merging to `main`
deploys. Read `CLAUDE.md`, then `docs/HANDOFF.md`, before changing anything — they are
authoritative on invariants and live state. Develop on your designated branch, commit
with clear messages, push when complete. Do **not** merge, deploy, alter production
credentials, or open PRs unless asked. Build and test through the Makefile
(`make test` / `make lint` — CI runs the same targets).

## Objective

Add Indian litigation as a source of insider-threat behavioral evidence — a new
**ingestion lane inside this repo** (never a separate system; "no second storage system"
is an operator boundary) feeding the existing score → classify → enrich pipeline and the
EVIDENCE product. Identifiers: `source_id = indiankanoon-judgments`,
`source_name = Indian Kanoon`, `channel = filings`, country `IN`.

This is behavioral corpus research, never employee screening. Hard boundaries
(permanent): no employee-name searches, no nationality inference, no person/persona
graphs or cross-case entity resolution, no individual risk scores, roles never
individuals, alleged/reported/admitted/adjudicated never conflated, enrichment history
append-only, match-signal text in `RawArticle.content` never `summary`, secrets only via
`shared/settings.py`/env, no eCourts CAPTCHA automation or scraping, no LLM calls at
read time.

## Deliver in three phases, in order, as separate PR-sized chunks

### Phase 1 — spend-gate integrity (before any new lane; this is HANDOFF item 10, the ACTIVE work item)

Verified state as of 2026-08-22 — trust these, they were confirmed at file:line:

- The enrichment spend policy, prompt-size cap, and head+tail packing all derive channel
  from `resolve_channel(source_id)` (`shared/schemas/articles.py:41`), which returns
  `"filings"` only when `"courtlistener"` is in the id. `RawArticle.channel` is ignored
  by the gate (`shared/agents/summarize.py` ~:340, :156, :352; `shared/llm/base.py`
  ~:403). Existing `canlii-*` filings rows therefore bill under the NEWS gate today; any
  `indiankanoon-*` row would too.
- The filings gate (`summarize.py::qualifies`, ~:110-128) requires body ≥ 1500 chars plus
  `_body_has_itm_signal` = **any one ITM alias** — too weak (58% of post-gate enrichments
  adjudicated non-insider per the 2026-08-04 audit). `INSIDER_FRAMING_KEYWORDS`
  (`shared/itm/aliases.py:273`) never reaches the gate.
- The gate scans `clean_text = title + summary + content`, and lanes store their query
  tag at the head of `content` — a query tag phrased in insider terms can self-satisfy
  the signal check.

Work: (1) make the gate honor filings for non-CourtListener court sources (stored
channel or a filings source-prefix set including `canlii-*` and `indiankanoon-*`) across
`qualifies()`, the cap selection, and head+tail packing; (2) require an
`INSIDER_FRAMING_KEYWORDS` hit in the body, per HANDOFF's proposed fix; (3) strip a
source's own query-tag line before the in-body signal check; (4) write a dry-run replay
script (per HANDOFF: ~937 stored non-insider vs ~540 insider filings rows) for the
operator to run on the Spark host — the corpus is not reachable from a web sandbox — and
treat its false-negative report as a review gate; (5) extend `tests/test_channel.py` and
the gate tests. Note: this changes CanLII billing behavior by design — the operator has
blessed the direction, but state the blast radius in the PR body.

### Phase 2 — the Indian Kanoon lane (dark: disabled by default, fixture-tested)

Mirror the CourtListener two-module pattern (`apps/aggregator/courtlistener.py` +
`courtlistener_pipeline.py`; `docs/courtlistener-ingest.md` documents it).

Verified Indian Kanoon API facts — do not re-derive, and do not contradict without
fetching the live docs:

- **All calls are POST** (params still ride the URL query string). Auth
  `Authorization: Token <key>`. Endpoints: `/search/?formInput=<q>&pagenum=<n>`
  (+ optional `maxpages`), `/doc/<tid>/` (+ `maxcites`/`maxcitedby`), `/docmeta/<tid>/`,
  `/docfragment/<tid>/?formInput=<q>`, `/origdoc/<tid>/`. Doc ids are numeric; public
  link `https://indiankanoon.org/doc/<tid>/`.
- `pagenum` is zero-based, 10 results/page, `maxpages` capped at 100 (~1000
  results/query) — historical coverage must slice by date windows, not page deep.
- Operators `ANDD`/`ORR`/`NOTT` are case-sensitive and space-delimited; implicit AND
  between bare words; **parentheses/grouping are undocumented — use flat ORR chains or
  multiple queries**, never CourtListener-style grouped booleans. Filters: `doctypes:`
  (court values like `supremecourt`, `delhi`, aggregate `highcourts`, comma-separated),
  `fromdate:`/`todate:` in **D-M-YYYY, day-first, not zero-padded**, plus `title:`,
  `author:`, `bench:`, `sortby: mostrecent`.
- Pricing: prepaid INR credits (₹500 sign-up credit; ₹10,000/month free for
  admin-verified non-commercial use; ~₹0.50/search-page and ₹0.20/doc are unofficial
  third-party figures). Parameterize all budget logic; hardcode no prices. Because calls
  are metered, treat spend caps at PACER standard: credentials AND a budget ceiling in
  cents AND per-run caps AND dry-run support — CLAUDE.md's "never add another purchase
  path" invariant means this needs explicit operator-visible budget machinery, not just
  a fetch cap.
- Attribution: "powered by IKanoon" logo is required and the terms explicitly cover
  RAG/LLM use (direct display → logo above results; integrated use → About/docs). The
  exact wording, official logo asset, and the **storage/redistribution clause are
  unverified** — the lane stays **activation-blocked** until the operator reads the terms
  verbatim; ship attribution scaffolding and document the unresolved step. Never commit
  an unofficial logo substitute.
- Claude Code web sandboxes cannot reach `api.indiankanoon.org` (egress-blocked). All
  tests are fixtures; never claim live-API behavior was verified unless an authenticated
  call actually ran.

Repo contracts the lane must satisfy (all verified — follow them exactly):

- The backfill machinery is CourtListener-specific (`courtlistener_pipeline.py:226-461`):
  replicate the whole contract for IK — own single-line sentinel
  (`IndianKanoon query: <q>` in `content`, never `summary`), attempt-state markers with a
  7-day retry window, content-append on body arrival, `refresh(force=True)` for a fresh
  `ingested_at`, and a `_clear_llm_fields`-equivalent that **never clears
  `enrichment_history`**. Skipping the clear step pins the thin metadata-only enrichment
  forever via the carry-forward cache-hit
  (`shared/agents/article_processor.py:211-235`).
- Lane health needs an explicit `expected_lane_specs` branch gated by the enable setting
  (clean absence when disabled — see the `social_ingest_enabled` precedent at
  `apps/aggregator/lane_health.py:116`), an include flag threaded through
  `apps/aggregator/run_all.py`, and an `_infer_kind` prefix entry.
- Story clustering reads literal `"Court: …"` / `"Docket: …"` lines from `summary`
  (`shared/utils/story_key.py:63-90`) — the mapper must emit them or clustering degrades
  to title+day.
- Forward ingest: use explicit overlapping `fromdate`/`todate` windows (IK results are
  relevance-ordered, so the repo's watermark convention doesn't transfer — document this
  as a deliberate divergence). History sweep: build it **minimal** (rotation + cursor in
  `data/state/ingest_state.json`, hold on 429) — deep history is designated to the open
  dataset below, not to API paging.
- Settings as `INDIANKANOON_*` in `shared/settings.py` (disabled by default; no token →
  no network call; no history floor → no sweep); CLI subcommands `ingest_indiankanoon` /
  `sweep_indiankanoon_history` / `backfill_indiankanoon_text` following the
  `__main__.py` three-part convention; `.env.example` + `.env.spark.example` entries;
  `docs/india-courts-ingest.md`; CLAUDE.md + HANDOFF updates in the same PR.
- Query pack: hand-authored Indian insider-behavior clusters (personal-email
  exfiltration, pen-drive copying, source-code/customer-data theft, notice-period
  removal, moonlighting/dual employment, deletion/formatting, WhatsApp/personal-cloud
  sharing, criminal breach of trust, IT Act §43/§66), phrased as quoted phrases + flat
  ORR chains, constrained by `doctypes:`. No broad standalone terms.
- Quick wins (operator-approved, config-only): verify whether Indian
  regulator/prosecutor feeds exist (CBI, ED, SEBI, CERT-In) and add working ones to
  `config.py` per the existing phase-0 prosecutor-feed pattern; add India-scoped queries
  to the `web_keywords` lane. These ship coverage while the IK lane is dark.
- Tests in `tests/test_indiankanoon.py`: auth header, no-token no-call, query encoding,
  zero-based pagination, date-window construction, tolerant parsing, tid links, dedup
  across queries, query marker in `content` not `summary`, HTML→text, head-and-tail
  truncation with omission marker (unit-test exact behavior), 403/429/5xx/timeout/
  `Retry-After`, window rotation, cursor hold, caps, backfill retry interval. Fixtures
  only — no copyrighted full judgments, no live calls.

### Phase 3 — provenance, posture, and surfaces

- Add an optional `LegalMetadata` object (country_code, jurisdiction, court_name,
  court_level, document_kind, procedural_stage, case_number, cnr, source_document_id,
  decision_date, language, source_terms) to `RawArticle` and `ProcessedArticle`. It does
  **not** flow automatically — thread it explicitly at: the lane mapper, `_node_assemble`
  (`article_processor.py:325-348`), `SearchHit` + `apps/search/index.py::_to_hit`, the
  ledger row projection (`apps/search/service.py:175-184` currently passes only
  link/title/published/forensics), and the pure-stdlib Actions path
  (`scripts/evidence_ledger.py`). Storage back-compat is safe (pydantic defaults; JSONL
  loaders skip bad lines). Add an authored `source_id → country` fallback resolver
  (`courtlistener-*`→US, `canlii-*`→CA, `indiankanoon-*`→IN); explicit metadata wins.
  Never fabricate a CNR or case number.
- Indian legal postures: `legal_posture` is enforced in **three unlinked places** —
  `shared/schemas/forensics.py:84-94` (+ coercion at :519/:524, unknown→"unknown"), the
  prompt enum text in `shared/llm/base.py:295`, and `POSTURE_WEIGHT` in
  `shared/utils/evidence.py:47-56`. Critical: any posture missing from `POSTURE_WEIGHT`
  leaves claim_status **uncapped** in `case_strength` (evidence.py:344-358) — an
  FIR/bail/interim-injunction document whose methods the LLM stamped "adjudicated" would
  count court-proven. Add every new stage (fir_allegation, charge_sheet,
  interim_injunction, bail, quashing, writ_review, disciplinary_proceeding,
  arbitral_proceeding, civil_decree, trial_judgment…) at **all three sites** with weight
  below `POSTURE_ADJUDICATED_MIN_WEIGHT` (4) unless it is genuinely adjudicative, plus a
  cross-site drift-tripwire test (none exists). Update the enrichment prompt: recited
  allegations ≠ adjudicated; bail/quashing/interim relief ≠ conviction; disciplinary
  finding ≠ criminal conviction; `admitted` needs an admission/plea; ambiguity stays
  `unclear`.
- `country_code` facet: four layers (predicate in `apps/search/index.py` alongside
  `_article_matches_channel`, pass-through in `service.py`, Query params in `api.py` on
  `/articles`/`/search`/`/sources`, UI wiring) plus `/feed.xml` parity.
  `GET /export/articles` exists (NDJSON, `EXPORT_API_TOKEN`-gated) but filters only
  min_score/since/itm_alignment — a country filter is new work; mind
  `EXPORT_SCHEMA_VERSION` (`insider-intel.export.v5`, test-pinned).
- UI: IN jurisdiction chip in `buildArticleRow`'s metaParts idiom with a `data-tip`;
  "every page teaches itself" applies (purpose line, tooltips, empty states;
  `tests/test_site_guide.py` fails bare tabs); ABOUT-page attribution extends
  `tests/test_about_page.py` (minimal-pane, attribution-lines, no-corpus-digits
  contracts); `ui-smoke` must pass at 390/1280 and Playwright evidence goes in the PR
  body. `web/findings.json` needs no rewrite (static, publish-by-merge).
- EVIDENCE ledger jurisdiction breakdown: deterministic, pure-stdlib (the module
  deliberately imports nothing beyond stdlib), small-n floor (`SMALL_N_FLOOR=10`) and
  case-strength separation preserved; jurisdiction = source court system, never actor
  nationality; update basis/limitations language to say different jurisdictions
  contribute different document types and procedural stages.

## History strategy (document in `docs/india-courts-ingest.md`; do not build now)

There is nothing to purchase. The bulk corpus is the **Indian High Court Judgments**
open dataset (AWS Open Data Registry, `vanga/indian-high-court-judgments`): 17.8M
judgments, ~1.25 TiB, 25 High Courts, CC BY 4.0, AWS-sponsored free access, **updated
daily**; parquet metadata carries `cnr`/`decision_date`/`order_number` with
`year/court/bench` partitioning; a Supreme Court companion dataset exists. This is the
designated deep-history path (clear license including permanent GCS storage; no depth
cap; zero spend) — Parquet-first selection, targeted court/year partitions, dedupe by
`(cnr, decision_date, order_number)`. Indian Kanoon remains the discovery engine (only
affordable full-text query service); the dataset is acquisition/history. Also document
why eCourts CAPTCHA automation and commercial APIs are excluded.

## Operator pre-flight (don't block coding; record gaps in the final report)

Pending from the operator: the verbatim `api.indiankanoon.org` terms/pricing/
documentation text (sandbox can't fetch them; the storage clause is the activation
blocker), the official logo asset, an API token + egress allowlisting if live
verification is wanted, the Phase 1 dry-run execution on the Spark host, and budget-cap
values.

## Definition of done

Phases 1–3 implemented and pushed to the designated branch; lane disabled by default and
activation-blocked pending attribution/terms; all existing lanes still pass;
`make test` + `make lint` green; fixtures only (no live-API claims without a real
authenticated call); docs updated in the same PRs; no secrets committed; no
merge/deploy. Final report: architecture, files changed, settings added, exact test
results, unverified live behavior, attribution status, activation steps, limitations,
and any deviations from this brief with reasons.
