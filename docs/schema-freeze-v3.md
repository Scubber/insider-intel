# Enrichment schema freeze v3 (#10)

The rule since the cutover: every schema and prompt change batches into one
frozen version, then the corpus re-tags ONCE (#14). This document is that
freeze. Anything not listed here waits for v4.

**Version bump:** `ENRICH_SCHEMA_VERSION` 2 → 3. The bump is the sweep
trigger — the re-enrich selectors treat any record below the current version
as stale — so it ships in the same PR that arms #14's pilot, never alone.

## New fields (all default-safe, all nullable)

| field | type | contract |
|---|---|---|
| `actor_citizenship` | `str \| null` | Only from an EXPLICIT statement in the source ("citizen of India", "a Chinese national"). Never inferred from a name — a name is not evidence of nationality. Civil filings usually only plead *state* citizenship; record `"US (state pleaded)"` for those. Operator ask, 2026-08-19 (Azurity thread). |
| `industry` | enum | The victim organization's sector. Seed enum: `financial-services \| healthcare \| technology \| defense \| manufacturing \| energy \| retail \| public-sector \| professional-services \| other \| unknown`. Financial services first per operator priority. |
| `tool_mentions[].role` | enum | `caught \| bypassed \| misused \| traced` — per named product in the text. Fills the TOOLING table's end-state columns (operator-approved v8 spec). Tool names feed the catalog's candidate mining. |

## Removed

- `hunt_queries` — dead weight (operator call). `hunt_terms` STAYS; it feeds
  hunt synthesis.

## Prompt contract v3 (changes to `ENRICH_SYSTEM_PROMPT`)

1. **Verbatim hard demand** — evidence quotes are exact substrings or empty;
   measured drift: verbatim fell 89% → 79% under the v2.1 field demands
   (R3 control, 2026-08-20). #16's guided JSON enforces shape; this enforces
   content.
2. **Calibrated-confidence rubric** — kills the 0.95 tic. Anchor bands:
   0.9+ court-adjudicated facts; 0.7–0.9 charged/alleged with documents;
   0.4–0.7 news-sourced; <0.4 thin or secondhand.
3. **Exfil rewording** — the one field the v2.1 demands did NOT move
   (13% fill, flat). Current phrasing asks for "channels"; v3 asks
   concretely: "every route data left by — name the service, device, or
   method the text states (personal Gmail, USB drive, Dropbox, printouts,
   photographs)".
4. **Verdict guard audit** — the case-facts block must not imply case-ness.
   Measured: stock R3 held FP=2 under v2.1, so no regression today, but two
   candidate models went FP-happy (12–13 FP/40) — the sweep runs on whatever
   serves, so v3 restates: "fill case facts ONLY when is_insider_case is
   true; a filled field is never a reason to call something a case."
5. **ITM 2.12 rubric line** — technique adjudication references the current
   matrix (index already at 2.12; the prompt's guidance text lags).

## Riding with the freeze (#16, same PR series)

- **Guided JSON** (vLLM structured output): the model structurally cannot
  omit keys or emit malformed records. Fixes the parse-failure class in both
  A/B arms and makes per-field fill a content problem only.
- Token cuts where the contract allows.

## Measurement gates (added to `ab_thinking_report`)

- Per-field fill-rate criteria (detection / exfil / outcome / timeframe /
  motive) — the gap that let field-skipping ship in the first place
  (Qwen detection: 3% vs Haiku 49%, caught only by corpus audit).
- Verbatim floor: ≥ 85% or the round fails.
- Verdict accuracy vs manifest labels (not stored baselines — the requeue
  taught us stored baselines churn).

## Sweep plan (#14, gated on this freeze merging)

1. Serving model runs the 40-case gold set under v3 → report vs the v2
   table. KEEP_THINKING re-check included.
2. 50-case pilot sweep → recommendation → operator accepts (per standing
   agent-assisted-review protocol).
3. Full re-tag: ~1,970 enriched cases at the serving model's pace
   (Nemotron ≈ 61s/case ≈ 33 GPU-hours → ~4–5 nightly cycles at cap 160,
   or one weekend drain run in gaps). Backfills citizenship, industry, and
   tooling role columns corpus-wide; also unifies the Haiku/Qwen/Nemotron
   provenance mix under one contract.
4. Post-sweep: re-derive the SIG threshold analysis (the gate lexicon and
   score distribution shift), refresh the tooling table columns, and run
   the cross-channel case-linker feasibility pass on the new actor fields
   (Apple/Chang Liu duplicate class — operator finding, 2026-08-21).

## Explicitly OUT of v3 (v4 candidates)

- Cross-channel case-linker (design depends on v3's actor fields landing).
- Gate-lexicon tuning (jargon demotion, slop-admitting technique
  down-weights) — changes ingest scoring, not enrichment schema; separate
  change window so sweep comparisons stay clean.
- Findings-as-templates (#9) — consumes v3 fields; doesn't shape them.
