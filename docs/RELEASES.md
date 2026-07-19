# Releases

Milestone checkpoints. Each entry is an annotated git tag on `main`; notes
cover everything since the previous tag.

## theme-release-0.2 — "Dossier" (2026-07-19)

Tag target: `51bfec1` (merge of #61). Everything since `mvp-prototype-0.1`
(2026-07-13 → 2026-07-19, 76 commits, PRs #5–#61).

### Dossier design language & themes

- Full UI redesign to the "Dossier" classified-file look; new default (#7, #8)
- Dark Diablo Dossier theme pack: all 29 handoff themes on the site and in the
  design system; **Dossier Sage is the new default**; the wanted set ranks
  first in the picker (#58)
- Diablo black-iron theme (#38); view tweak controls — density / stacked
  layout / redaction (#59)
- Analyst-note usability: inline SHOW MORE/LESS, ⧉ copy-card button (#60),
  "truncated at source" notice on feed-cut summaries (#61)
- Observed-only ITM INDEX rail, case-filtered techniques, full matrix behind
  MATRIX nav (#9, #10); per-case "Show hunt report" button (#39)

### Design system (Claude Design integration)

- `insider-intel-dossier-ui` React package mirroring the site's design
  language, synced to a claude.ai/design project via design-sync (#34)
- ThemeSelect switcher component (#37); all 29 pack themes in the library (#58)

### Enrichment & LLM pipeline

- Hunt report pivoted to **ingest-time enrichment**: one unified LLM call per
  article → analyst note + forensic record + hunt queries; the extract
  endpoint spends no LLM credits (#31, #35, #36)
- Multi-provider fallback chains — Anthropic, OpenAI, Gemini, xAI/Grok, SOL —
  with declarative config and dead-provider fall-through (#33, #45, #46, #47)
- Novel-technique discovery pass (#43); evidence-rigor fields (#40); prompt
  sharpened for insider-threat relevance + tactical TTP specificity (#48)
- Full-text court-filing enrichment at higher throughput (#42); filings
  outrank news in the backfill (#52); backfill checkpointing so timeouts never
  re-bill (#54); the news batch cannot spend the backfill's budget reserve
  (#53); LLM spend deduped per story cluster (#57)

### Ingestion & data

- Case records ingested with LLM matrix mapping; evidence-gated extract
  floor (#11)
- CourtListener: full text + budget-capped PACER purchasing (#12), throttle
  matching and account-budget pacing (#14, #16, #17), decade-deep historical
  sweep of insider prosecutions (#19)
- X lane consumer-key bearer minting with free-tier cadence guard (#13); new
  threat-research feeds (#5, #6); demo snapshot decoupled from the live
  product

### Operations

- GitHub-dispatchable workflows: refresh-corpus (#15), cancel-refresh (#56),
  live refresh progress watch (#55), probe-extract live smoke (#32, #51)
- docs/ROADMAP.md tracking shipped / in-flight / next (#20, #44)

### Known issues (deferred, not blockers)

- Assorted UI bugs observed post-release — to be enumerated and filed as
  issues.

## mvp-prototype-0.1 (2026-07-13)

First tagged prototype: RSS/Feedly/CourtListener ingestion, ITM alias
matching, GitHub Pages UI, Cloud Run API, 6-hourly corpus refresh.
