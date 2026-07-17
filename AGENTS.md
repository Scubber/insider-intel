# Agent instructions

Read **[CLAUDE.md](CLAUDE.md)** — the agent operating manual for this repo —
before making changes. It is tool-agnostic despite the name: architecture map,
everyday commands, production invariants, and hard-won gotchas.

Non-negotiables (full rationale in CLAUDE.md):

- This repo is **in production**; merging to `main` deploys.
- Build/test through the Makefile (`make up / test / lint`) — CI runs the
  same targets.
- Corpus lives in GCS, never in images; config flows only through
  `shared/settings.py`; match-signal text goes in `RawArticle.content`,
  never `summary`.
- Don't remove the `/extract/ttps` rate limiter; keep workflow actions
  SHA-pinned; secrets only in Secret Manager/env.
