# Voya Financial — top insider threat profiles from the insider-intel corpus

**Status (2026-09-22): Phase 1 delivered, Phases 2–3 blocked on corpus access.**
Read `phase1-inventory.md` first; §6 states the blocker and the options.

| File | What it is | Phase |
|---|---|---|
| `phase1-inventory.md` | Corpus inventory, proposed tagging taxonomy, counts by sector, preliminary candidate list, blocker | 1 (done) |
| `methodology.md` | Taxonomy summary, profile construction rules, scoring rubric, sampling, bias statement | 1 draft; extended in 2–3 |
| `inputs/2026-09-22-corpus-diagnostics.md` | Every number used in Phase 1, copied from the dated read-only Actions runs (run ids inside) | 1 |
| `tools/tag_cases.py` | Stdlib derived-layer tagger; produces the tagged case JSONL and the `evidence.csv` skeleton | ready for 2 |
| `report.md`, `profiles.json`, `evidence.csv`, `annex-non-us.md` | Ranked profiles and their receipts | 3 (not started) |

Rules carried from the task and the repo: every claim traces to a case id
in `evidence.csv`; roles, never individuals; adjudicated, alleged and
reported are never pooled; profiles under 5 supporting cases are flagged,
never padded; distinct archetypes are never merged to reach ten. This
folder is a dated analysis, not a product surface: its numbers are frozen
at their AS OF line, and the live figures stay on the EVIDENCE page.
