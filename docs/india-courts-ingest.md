# How the ingest finds Indian court judgments

This explains where Indian court filings in the corpus come from: the data
source, how coverage works, the exact matching rules, and how a judgment goes
from a dataset row to a fully-enriched card.

**One-line answer:** it is a **local lexicon scan over a free open dataset** —
no search API, no per-call spend, no scraping. We download judgment PDFs from
the CC BY 4.0 eCourts open dataset, extract their text on our own hardware,
and keep only the judgments whose text matches hand-authored insider-behavior
patterns. Coverage is both **forward (daily)** and **retroactive (a backward
year walk to 2000)**.

All code lives in `apps/aggregator/indiacourts.py` (dataset client, PDF text
extraction, the insider lexicon, OCR backend hook) and
`apps/aggregator/indiacourts_pipeline.py` (the three ingest jobs).

## The data source ($0, CC BY 4.0)

The **Indian High Court Judgments** dataset on the AWS Open Data Registry,
maintained by the `vanga/indian-high-court-judgments` project: ~17.8M
judgments across all 25 High Courts, scraped from eCourts, **updated daily**,
with AWS sponsoring storage and egress. A companion Supreme Court dataset
lives on the same registry (a future source).

- License: **CC BY 4.0** — storing full text permanently and re-serving it is
  expressly permitted; attribution is a credit line (see ABOUT), not a logo.
- Layout (verified live 2026-08-22):
  `metadata/parquet/year=<Y>/court=<C>/bench=<B>/metadata.parquet` (per-
  partition metadata: `cnr`, `decision_date`, `pdf_link`, court, judge,
  disposal) and `data/pdf/.../<CNR>_<order>_<date>.pdf` (individual judgment
  PDFs, directly fetchable). Court codes are `27~1` in metadata, `27_1` in
  paths.
- **Why not Indian Kanoon / eCourts portals / commercial APIs:** operator
  decision 2026-08-22 — no site association with Indian Kanoon and no metered
  spend; eCourts portals are CAPTCHA-gated (automation excluded on purpose);
  commercial legal APIs are enterprise-priced for a different audience. The
  open dataset makes all of them unnecessary.

## What is scanned (coverage = the lexicon)

There is no server-side search, so **coverage equals the pattern list** in
`indiacourts.py::INSIDER_PATTERNS`: hand-authored compound patterns (every
term must appear) for the Indian insider-behavior classes — departing-employee
data theft, pen-drive/USB copying, personal-email/WhatsApp/cloud exfiltration,
notice-period removal, moonlighting and dual employment, deletion/concealment,
criminal breach of trust, IT Act §43/§66 prosecutions. Broad standalone terms
("employee", "fraud") are excluded by design; a new insider pattern needs a
new entry (then a re-walk picks up history).

The matched pattern labels are written as an `IndiaCourts match: …` line at
the head of `RawArticle.content` — scored but never displayed, and the
enrichment spend gate strips it before its body checks so a marker can never
qualify its own document.

## The three ways judgments enter the corpus

### A. Forward daily diff — `run_indiacourts_ingestion`

The dataset regenerates daily. Each refresh lists the current and previous
year's partitions; a partition whose parquet **ETag is unchanged** since the
last completed pass is skipped outright. Changed partitions are diffed against
the per-partition done-set, and only **new** judgments are fetched, extracted,
and scanned (capped by `INDIACOURTS_MAX_PDFS_PER_RUN`).

### B. Retroactive year walk — `run_indiacourts_history_sweep`

A cursor walks **backward one year at a time** from two years behind today to
`INDIACOURTS_HISTORY_FLOOR` (default **2000** — the IT Act era; dataset volume
starts ~2004, so this is effectively full coverage). Within a year, courts
process **hub-first** (Delhi, Bombay, Karnataka, Madras, Telangana, Punjab &
Haryana, Gujarat, Calcutta, Allahabad — `INDIACOURTS_COURT_ORDER`), so insider
signal reaches EVIDENCE fastest; every court is covered eventually (operator
decision: **no court exclusions**). The cursor advances only when every
in-scope partition of the year is complete — a capped or failed run resumes
the same year via the per-partition done-sets, so nothing is skipped.

### C. Pending retries — `run_indiacourts_extract_pending`

PDFs that fail (404, oversize, corrupt) or have no usable text layer
(scanned documents awaiting OCR) are parked in a pending queue with a
cool-down (`INDIACOURTS_RETRY_DAYS`, default 7) and retried in bounded
batches. When `INDIACOURTS_OCR_COMMAND` is configured (an
`<command> <pdf-path>` wrapper printing text to stdout — pick the tool via an
OCR bench on sparky; olmOCR-2-7B is the researched default, DeepSeek-OCR 2
the speed option, Surya the Indic-script option), scanned PDFs OCR inline.

## Design vs the CourtListener lane

| | CourtListener | IndiaCourts |
|---|---|---|
| Discovery | server-side query API | local lexicon scan |
| Stub rows | yes (metadata first, text backfilled) | **no** — matches enter WITH text |
| Re-enrichment reset | `_clear_llm_fields` after backfill | not needed (rows arrive complete) |
| Spend | metered API + PACER purchases | $0 |
| Rate limits | 10/min shared throttle | none (AWS-sponsored S3) |
| Disk | n/a | rolling buffer — PDFs processed one at a time, never accumulated |

State lives under `data/state/indiacourts/`: one JSON per partition (etag +
done basenames + complete flag), `pending.json`, and the history cursor in
the shared `ingest_state.json`.

## Enrichment and posture

Matched judgments carry `channel="filings"` and the `indiacourts-` prefix
resolves to the **filings** spend gate (body floor + in-body alias AND
framing signal — see the 2026-08 gate tightening). Enrichment runs on the
normal chain (sparky's local model in prod — $0). Indian procedural posture
(FIR, charge sheet, bail, quashing, interim injunction, writ review…) is
weighted **below the adjudicated floor** in the EVIDENCE case-strength system
— a bail order reciting rich forensic detail stays *alleged*, never
court-proven (see `shared/utils/evidence.py::POSTURE_WEIGHT`).

## Selection bias and honesty

- Coverage equals the lexicon; there is no semantic discovery at the scan
  stage. Absence of a pattern ≠ absence of the behavior in India.
- High Court judgments are appellate/writ-heavy and under-represent district
  courts; different jurisdictions contribute different document types, so
  cross-country rate comparisons carry the selection caveat (methodology
  tooltip on the EVIDENCE page).
- Jurisdiction = the court system of the record, never the actor's
  nationality.

## Operational activation (sparky)

1. Bench an OCR backend on the box; set `INDIACOURTS_OCR_COMMAND` (optional —
   without it, scanned PDFs simply wait in pending).
2. Set `INDIACOURTS_ENABLED=true` in `.env.spark` (the lane is disabled by
   default everywhere; the GCP rollback job must not run it).
3. Optionally restrict scope while proving the lane: `INDIACOURTS_COURTS`.
4. Watch `/lanes/health` — the lane reports as `indiacourts-judgments`
   (kind `court`); history/extract report as dynamic rows.
5. Confirm free disk covers the rolling buffer (a few GB) — tars are never
   downloaded, PDFs are processed one at a time.

## Troubleshooting

- **Lane absent from `/lanes/health`** → `INDIACOURTS_ENABLED` is false
  (disabled = cleanly absent, by design) or the refresh ran with
  `--skip-indiacourts`.
- **`pending.json` growing** → OCR command unset or failing; run
  `python -m apps.aggregator extract_indiacourts_pending -v` and read stderr.
- **A partition never completes** → check its state file under
  `data/state/indiacourts/`; deleting the file forces a clean re-walk of that
  partition (idempotent — the store dedupes by link).
- **Zero matches for weeks** → normal for small courts; check
  `[indiacourts]` log lines (`pdfs=… matches=…`) before suspecting breakage.

## Future sources (documented, not built)

- **Supreme Court companion dataset** (same registry/layout) — add codes when
  the lane has proven itself on High Courts.
- **Bulk tars** (`data/tar/…`) — a per-byte-cheaper history path if per-PDF
  throughput ever becomes the constraint.
- **Indian regulator/prosecutor feeds** (CBI, ED, SEBI, CERT-In) — candidate
  phase-0-style additions; their sites were unreachable from the build
  sandbox, so feed URLs remain unverified. Verify from an unrestricted
  machine and add to `config.py` like the AFP/NCA feeds.
- **UK Find Case Law** remains the planned phase-1 international lane
  (docs/HANDOFF.md item 8); the jurisdiction plumbing added for IN serves it.
