# Enrichment schema v4 — candidates ledger

**Status: NOT frozen. v3 is the live contract** (`ENRICH_SCHEMA_VERSION=3`,
[schema-freeze-v3.md](schema-freeze-v3.md)). This file is the ledger of what
a v4 bump would carry, and of the fields that landed early as ADDITIVE
fields at v3 under the overlay rule below. Nothing here changes the tier
of any stored generation.

## Why additive fields exist

A schema bump re-tiers every filing through the reenrich lane
(`apps/aggregator/reenrich.py::select_missed_filings` keys on
`schema_version < ENRICH_SCHEMA_VERSION`) and, because select-best is
schema-tier-first (PR #242), every row's projection re-adjudicates as its
first v(N+1) generation lands — verdicts churn corpus-wide for weeks at 60
filings a night. A field that adds a fact without changing how a case is
judged does not need that. It lands at the current tier, defaults to
`None`, and the projection fills it from history.

## The additive-field overlay rule

`shared/schemas/forensics.py`: `ADDITIVE_FIELDS` and
`project_additive_fields(history, best)`.

1. Select-best runs first, exactly as before (`select_best_enrichment`):
   newest schema tier only → verdict by the higher-confidence side → richness
   within the verdict → newest on ties. Additive fields are **not** an input
   to any of those steps. The ordering is byte-identical with or without the
   overlay (pinned by
   `tests/test_projection_freeze.py::test_select_best_ordering_byte_identical_with_and_without_overlay`).
2. For each additive field that is `None` on the selected generation, the
   projection takes the value from the **newest generation at the same top
   tier** that carries a non-null value, and stamps `<field>_source =
   {"model", "extracted_at"}` from that donor. A value the selected
   generation already has is kept and carries no `_source` stamp.
3. The overlay MAY touch: the additive field and its `_source` stamp on the
   projected `forensics` copy. It MAY NOT touch: `is_insider_case`,
   `confidence`, `methods`, `ai_summary`, `outcome`, `schema_version`,
   `model`, `extracted_at`, or anything else `enrichment_richness` or
   `_selection_key` reads. It never mutates history or the selected record —
   it returns a copy.
4. No-op when no same-tier generation carries the field: the projection is
   the selected generation's forensics, unchanged and identical by object.
5. Both projection sites apply it: the graph's `_emit_selected`
   (`shared/agents/article_processor.py`) and the backfill write in
   `apps/aggregator/process_pipeline.py`. A new projection site must call
   it too.

Coercion of an additive field is **null-preserving**: off-enum or missing →
`None`, never `"unknown"` (contrast `industry`). Silence means the
generation never answered the question, which is exactly what lets the
overlay fill it from one that did.

Backfilling an additive field is by the field's own absence, any channel:
`python -m apps.aggregator backfill_field --field <name> [--dry-run]
[--limit N]` (`reenrich.py::select_field_backfill_targets`; sparky-ops
`backfill-field-dryrun` / `backfill-field confirm=RUN`). The clear reuses
`_clear_llm_fields` — projection pointer only, history preserved — so the
nightly sweep re-enriches the row and the overlay fills the value.

## Landed additive at v3

- `actor_employer_sector` — **LANDED additive at v3 on 2026-09-04 via the
  overlay rule; promote to required at v4.** The sector of the organization
  that EMPLOYED the insider (INDUSTRIES enum, explicit statements only);
  `forensics.industry` stays the VICTIM organization's sector. The two
  diverge for tippees, contractors, and law-firm/advisor insiders (a
  staffing-firm contractor placed at a bank → professional-services). Prompt
  rule in `shared/llm/base.py`; provenance stamp
  `actor_employer_sector_source`. Backfill targets: verdict-true, v3,
  `None` field, victim industry in {financial-services, unknown} by default.

## Explicitly OUT of v3 (v4 candidates)

Carried from [schema-freeze-v3.md](schema-freeze-v3.md):

- Cross-channel case-linker (design depends on v3's actor fields landing).
- Gate-lexicon tuning (jargon demotion, slop-admitting technique
  down-weights) — changes ingest scoring, not enrichment schema; separate
  change window so sweep comparisons stay clean.
- Findings-as-templates (#9) — consumes v3 fields; doesn't shape them.

## When v4 actually freezes

- Promote every "landed additive" field above to required in the reply
  schema and the specimen; its coercion may then stay null-preserving or
  clamp, decided per field at freeze time.
- Bump `ENRICH_SCHEMA_VERSION`, update `docs/schema-freeze-v4.md` from
  ledger to freeze record, and arm the reenrich lane knowing it will re-tier
  the whole filings corpus.
