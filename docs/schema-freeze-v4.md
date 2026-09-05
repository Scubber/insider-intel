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
   projection takes the value from the **newest generation at history's top
   tier** that carries a non-null value — **preferring donors whose
   `is_insider_case` equals the selected generation's**, falling back to any
   same-tier donor — and stamps `<field>_source = {"model", "extracted_at"}`
   from that donor. Ties on `extracted_at` (or missing stamps) resolve to the
   later history position, so the pick is deterministic. A value the
   selected generation already has is kept and carries no `_source` stamp.
   The `_source` stamp lives only on the projected copy — never inside
   `enrichment_history`.
3. The overlay MAY touch: the additive field and its `_source` stamp on the
   projected `forensics` copy. It MAY NOT touch: `is_insider_case`,
   `confidence`, `methods`, `ai_summary`, `outcome`, `schema_version`,
   `model`, `extracted_at`, or anything else `enrichment_richness` or
   `_selection_key` reads. It never mutates history or the selected record —
   it returns a copy.
4. No-op when no same-tier generation carries the field: the projection is
   the selected generation's forensics, unchanged and identical by object.
5. There is ONE projection entry point — `project_from_history(history)`
   (select-best, then the overlay, then the derived `case_record`) — and
   both writers use it: the graph's `_emit_selected`
   (`shared/agents/article_processor.py`) and the backfill sweep in
   `apps/aggregator/process_pipeline.py`. Before 2026-09-04 the sweep wrote
   the freshly produced generation straight to the row, bypassing
   select-best, so a thin or verdict-flipped re-enrich gutted a rich record
   until the next graph pass. A new writer must call `project_from_history`.
6. `project_additive_fields` anchors the tier on history's maximum
   `schema_version` and raises if `best` is not at it — impossible via
   `project_from_history`, a bug anywhere else.

Coercion of an additive field is **null-preserving**
(`coerce_additive_enum`): off-enum, missing, **or the literal `"unknown"`**
→ `None` (contrast `industry`, which clamps to `"unknown"`). A stored
`"unknown"` would be non-null and block the field forever — the backfill
skips non-null rows and the overlay would treat it as a real answer.
Silence means the generation never answered the question, which is exactly
what lets the overlay fill it from one that did.

## Backfilling an additive field: re-enrich WITHOUT clearing

`python -m apps.aggregator backfill_field --field <name> [--dry-run]
[--limit N] [--industry X ...]` (sparky-ops `backfill-field-dryrun` /
`backfill-field confirm=RUN`):

- Selection (`reenrich.py::select_field_backfill_targets`): verdict-true,
  current tier, `forensics.<field>` is None, victim industry in the set
  (default financial-services + unknown), ANY channel, newest first, and
  **passing `article_qualifies`** — the sweep's own spend gate — so nothing
  is queued that the sweep would refuse. The dry-run reports `queued` vs
  `skipped_by_gate` counts by channel/industry, never links or titles (it
  lands in CI logs). `--limit` defaults to `SUMMARIZER_BACKFILL_RESERVE`,
  the slice one sweep can actually spend.
- The mutating run writes the links to the queue file
  `data/state/field_backfill_targets.json` (`FIELD_BACKFILL_TARGETS_PATH`
  via `shared/settings.py`; a per-cycle `state/` file, never checked in).
  **Nothing is cleared** — every targeted row keeps its projection on
  EVIDENCE/TOOLING/the stream throughout.
- The sweep (`_backfill_summaries`) enriches queued links even though
  `forensics` is present (after never-enriched rows, before legacy
  upgrades), bills once, appends the generation, and re-projects via
  `project_from_history`. A link leaves the file only when its generation
  actually lands; a dead provider leaves it queued for the next cycle. A
  queued link that no longer passes the gate is dropped from the file with
  a log line — it keeps its record and is never stranded.
- The lane's clearing mechanism (`_clear_llm_fields`) is NOT used here; it
  stays as-is for its existing callers (full-text backfill, tier reenrich).
- **Already-asked guard (2026-09-05).** "Asked" means: some current-tier
  generation in `enrichment_history` has `extracted_at` at or after the
  field's contract stamp in `ADDITIVE_FIELD_CONTRACT_SINCE`
  (`shared/schemas/forensics.py`; for `actor_employer_sector` that is
  `2026-09-04T19:33:30Z`, the merge of PR #290 — the commit that put the
  field in the prompt). Such a generation was produced by a prompt that
  demanded the field, so a None there is the model's answer ("unknown",
  coerced to None by `coerce_additive_enum`), not a gap — a second ask is
  the same bill for the same answer (the 2026-09-04 drain re-enriched 229
  rows; 45 answered unknown and were listed again the next morning).
  `select_field_backfill_targets` reports them as `already_asked` (dry-run
  counts by channel/industry, never queued); the sweep drops an
  already-asked queued link from the file with a log count and no LLM
  call. The row keeps its projection and history throughout. Only a
  contract change (a new stamp, or the v4 bump) makes them askable again.

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
