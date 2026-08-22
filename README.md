# insider-intel

**Evidence-based insider-threat research, built from what actually reaches
court.**

insider-intel is a guidance product for insider-threat programs, built from
litigated court cases. It serves four jobs: **build a program, detect,
prevent, hunt** — and backs every claim with receipts from real filings.

The pipeline ingests litigated insider cases (US federal dockets and
opinions, international prosecutor and regulator feeds), insider-relevant
news, and long-form publications. Each case is forensically enriched by an
LLM once, at ingest. The corpus then answers questions a single case report
can't: how insider incidents happen, who commits them, which records detect
and convict them — and which methods don't yet map to anything in the
matrix (novel-technique discovery is a standing pass, not the mission).

**Live:** [insider-intel.net](https://insider-intel.net) (also
[intel.thederpweb.com](https://intel.thederpweb.com)) · API:
[api.intel.thederpweb.com](https://api.intel.thederpweb.com)

Everything is mapped to the
[Insider Threat Matrix™](https://insiderthreatmatrix.org/) (Motive · Means ·
Preparation · Infringement · Anti-Forensics).

---

## The EVIDENCE ledger

The **EVIDENCE page**
([insider-intel.net → EVIDENCE](https://insider-intel.net/#/evidence)) is
a continuously recomputed forensic aggregation across every method-bearing
case in the corpus:

- **Who?** Actor profile on two axes — function (executive/officer, manager,
  technical, sales/finance…) × employment state (current, departing, former) —
  as **roles, never individuals**.
- **How?** Technique prevalence by ITM theme, with the artifact families each
  technique leaves behind (device forensics, central audit trails,
  server/application logs, financial and public records).
- **How was it proven?** Every count is split by **case strength** —
  adjudicated/admitted vs alleged vs reported — and the two are never
  conflated. Percentages are suppressed below a small-n floor.
- **What detects it?** An evidence→ITM detection crosswalk ties observed
  record classes back to matrix detections, marking which are corroborated by
  real cases.

Published findings live in [`web/findings.json`](web/findings.json) and render
on the EVIDENCE page with their claim, the ledger data behind it, the honest
caveat, and program recommendations. Findings are operator-approved by merge —
the GitOps trail *is* the editorial record.

### Methodology and limitations

Court data is a biased sample: it over-represents what gets litigated. The
ledger treats that bias as measurable signal (selection bias is stated first
in the page's limitations), separates proof standards everywhere, refuses
persona/entity resolution by design, and keeps every number reproducible from
stored forensic records — **no LLM runs at read time**.

## What the platform does

| | |
|---|---|
| **Case stream** | Chronological insider-case reader with signal scoring, use-case + insider-type classification, and analyst notes. Cases the enricher itself adjudicates as *not* insider render as muted CONTEXT, hidden by default. |
| **Filings lane** | CourtListener RECAP dockets + opinions flagged by a hand-authored insider query lexicon; full-document bodies backfilled; targeted PACER purchasing (budget-capped) for high-signal stubs; CanLII and international prosecutor/regulator feeds. |
| **Forensic enrichment** | One LLM call per qualifying case at ingest produces the analyst note, a structured forensic record (actions, tools, quantities, typed observables, actor citizenship, victim industry, named-product roles), and an ITM adjudication — schema v3, with machine-verified evidence quotes. Every generation is stored append-only; the visible record is a select-best projection. |
| **Novel-technique discovery** | A second LLM pass over each filing's forensic record flags methods that don't map cleanly to existing ITM techniques — candidate tradecraft the frameworks haven't named yet. |
| **ITM matrix** | Five-theme technique browser; per-technique dossiers with related cases, detections/preventions, and corpus evidence tie-ins. |
| **Workbench** | Flag cases, extract a MODUS OPERANDI forensic case study assembled from stored forensics (no LLM spend) — per-case methods, observables, legal posture — with links into each technique dossier for hunting guidance. |
| **Tooling** | Security-product categories ranked by how much observed case volume their controls cover, with a per-product court-filing record — which products caught insiders, which were bypassed. |
| **Social + tips** | One-off URL flagging surfaces first-person confessions (overemployment, data theft). Scheduled social pulls are parked pending OAuth credentials. |
| **Syndication** | Atom feed (`/feed.xml`), one-way corporate export (`GET /export/articles`, NDJSON + bearer token). |

## Architecture

```
RSS / CourtListener / social / publications
   → ingest lanes → raw corpus (JSONL in GCS)
   → LangGraph processing: ITM alias match → score → classify → LLM enrich → embed
   → FastAPI (Cloud Run) : /articles /search /itm /evidence/ledger /tooling /extract/ttps
   → static UI (GitHub Pages) : STREAM | MATRIX | EVIDENCE | TOOLING | WORKBENCH
```

Production is fully automated: the corpus refreshes once daily from a local
DGX Spark that runs the enrichment model itself ($0 LLM spend), the site
boots from a static snapshot and swaps to live data, and every merge to
`main` deploys (keyless OIDC — no stored credentials). LLM spend is gated
by insider-signal checks; enrichment history is append-only, and records
re-enrich only when the analysis contract itself is upgraded.

For contributors and agents: [`CLAUDE.md`](CLAUDE.md) is the operating manual
(architecture, invariants, gotchas); [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)
covers the dev environment, [`docs/hosting.md`](docs/hosting.md) production,
and [`docs/HANDOFF.md`](docs/HANDOFF.md) the current live state.

## Run it locally

```bash
make up     # API :8000 + UI :5500 + Postgres sidecar
make test   # same targets CI runs — green local == green CI
```

Or bare-metal (Python 3.12+):

```bash
pip install -e ".[dev]"
python -m apps.aggregator all        # ingest → process → embed
python -m apps.search serve          # API → http://127.0.0.1:8000/docs
python -m http.server 5500 --directory web
```

Useful commands:

```bash
python -m apps.aggregator ingest_courtlistener        # pull flagged filings
python -m apps.aggregator backfill_courtlistener_text # fetch full document bodies
python -m apps.aggregator social suggest              # curated subreddit/X catalog
python -m apps.aggregator ingest_social_url <url>     # flag one post
python -m apps.aggregator process --force             # reprocess after config changes
python scripts/evidence_ledger.py data/processed/articles.jsonl  # ledger, offline
```

Copy `.env.example` → `.env` for configuration; everything flows through
`shared/settings.py`. No paid APIs are required to run the pipeline — LLM
enrichment, PACER purchasing, and social API auth are all optional and
key-gated.

## Adding sources

Edit `apps/aggregator/config.py` (80+ curated feeds across security, legal,
HR, and regulator domains) or pass a feeds JSON like
`apps/aggregator/feeds.example.json`. CourtListener queries live in
`courtlistener.py::DEFAULT_QUERIES` — a hand-authored insider lexicon
projected from ITM techniques.

## Tests

```bash
pytest
ruff check apps shared tests
python scripts/ui_smoke.py   # headless UX smoke over the real UI (Playwright)
```

## Design principles

- **Evidence over narrative** — every claim traces to stored forensic records
  and separates adjudicated from alleged.
- **Roles, never individuals** — no persona graphs, no entity resolution
  across cases.
- **Spend discipline** — LLM calls only where forensic extraction is
  plausible; each article billed once; all read paths are LLM-free.
- **One-way corporate boundary** — corporate tools pull OSINT out; this
  system never reads Graph/Teams/email/SIEM.
- **GitOps everything** — merge to `main` is the only deploy, approval, and
  publish mechanism.

## Attribution

Insider Threat Matrix™ is owned by Forscie Limited — see [`NOTICE`](NOTICE).
This project is not affiliated with or endorsed by Forscie.
