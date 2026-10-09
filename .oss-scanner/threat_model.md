# Threat model — insider-intel

Read with [`project.yaml`](project.yaml) and the [`Dockerfile`](Dockerfile)
in this directory. The repo-level manual is [`../CLAUDE.md`](../CLAUDE.md).

## What this project does and where untrusted input enters

insider-intel collects public insider-threat material — court filings, news,
social posts, reference publications — processes it into a corpus, and serves
that corpus through a public read API and a static web UI. It is in
production: the UI is on GitHub Pages, the API is a Cloud Run service with a
read-only mount of a Google Cloud Storage bucket, and the collection and
LLM enrichment pipeline runs once a day on an operator-owned machine ("the
DGX Spark") that pushes to the same bucket.

Untrusted input enters in four places. In order of exposure:

1. **The public API** (`apps/search/api.py`, FastAPI). Internet-facing,
   anonymous, no accounts. Query strings, JSON bodies and path segments on
   every `GET` are attacker-controlled. `POST /search`, `POST
   /articles/by-links` and `POST /extract/ttps` take anonymous bodies.
   `GET /export/articles` is behind `EXPORT_API_TOKEN`. The write and ops
   endpoints — `POST /reload`, the `/social/subscriptions` writes, `POST
   /social/ingest_url`, `POST /publications/ingest_url` — are behind an
   `ADMIN_API_TOKEN` bearer check (`_require_admin_token`). When the token
   is unset the gate is open; that is the documented local-dev default, and
   production always sets it. Treat the token as set when rating anything
   behind it.
2. **Collected content.** Every document the pipeline reads is written by
   someone else: RSS and Atom feeds (`feedparser`, `apps/aggregator/parser.py`),
   fetched HTML (`html_extract.py`), CourtListener JSON, Reddit and X JSON,
   PDFs through `pypdf` (`publication_extract.py`, `indiacourts.py`), the
   eCourts open dataset's parquet partitions through `pyarrow`, and scanned
   PDFs through an external OCR command (`ocr_pdf.py`;
   `INDIACOURTS_OCR_COMMAND` is operator config, split with `shlex`). A court
   filing quotes whatever the parties wrote, so corpus text can carry
   anything an adversary wants it to.
3. **LLM output.** Enrichment (`shared/agents/summarize.py`,
   `shared/llm/`) sends collected text to a model and parses the JSON it
   returns into a stored forensic record. A document can try to steer the
   model (prompt injection); whatever comes back is stored forever
   (`enrichment_history` is append-only) and later rendered. The model's
   output is untrusted input, not a trusted component.
4. **The web UI** (`web/app.js`, static, no build step). It renders corpus
   text — titles, summaries, analyst notes, quotes, hunt terms — into the
   DOM, with many `innerHTML` sites. A stored string that reaches the page
   as markup is the main client-side class to look for. Route state in the
   URL fragment (`#/technique/<id>`, `#/tooling/<id>`, `#/tools/<slug>`) is
   also attacker-controlled.

Everything else is operator-side: GitHub Actions workflows (SHA-pinned,
`workflow_dispatch` inputs, OIDC to GCP), `scripts/*.sh` that run unattended
on the refresh machine, and `shared/settings.py`, the only place
configuration and secrets are read from the environment.

## Components that matter most / least

**Most** — in scope, in production, reachable by strangers or by hostile
documents:

- `apps/search/` — the API service, its rate limiter, the token gates, the
  read-time aggregation (`service.py`, `tooling.py`, `vendor_mentions.py`,
  `ttp_extract.py`).
- `shared/utils/evidence.py` — the stdlib-only corpus reader every ledger
  surface runs on, including bare Actions runners.
- `web/app.js` — the shipped UI.
- `apps/aggregator/` parsers and fetchers — anything that turns bytes from
  the internet into stored rows, including the PDF, parquet and OCR paths.
- `shared/agents/`, `shared/llm/` — the enrichment call and the parsing of
  what the model returns.
- `shared/settings.py` — how secrets and tokens are loaded and validated.

**Less, but still in scope:**

- `scripts/*.py` and `scripts/*.sh` — operator tooling and the nightly
  refresh (`spark_refresh.sh`). They run with real credentials on the
  operator's machine, so a bug that leaks a key or a corpus write that
  overwrites history matters.
- `.github/workflows/` — dispatch inputs and what the job logs print (the
  repo is public; logs must never carry visitor IPs, addresses, or secret
  values).

**Out of scope** — do not spend time here:

- `design/`, `design-system/`, `preview/`, `docs/`, `dns/` — design files,
  a standalone offline preview, documentation, and DNS record declarations
  applied by a reviewed workflow.
- `shared/data/*.json` — authored taxonomy (the ITM catalog, tooling map,
  vendor aliases, peer sets). Treat as trusted, checked-in data.
- `tests/fixtures/` — sample inputs for the test suite.
- The LLM providers and CourtListener, Reddit, X, PACER and AWS Open Data
  themselves. Their responses are untrusted input to us; their own
  security is theirs.

## How to exercise it

The image is the repository at `/src` with every dependency installed and
a small synthetic corpus already seeded (`.oss-scanner/seed_corpus.py`: eight
fictional cases, no real people or companies). Nothing below needs a
network.

```bash
cd /src
pytest -q                                   # the full suite, ~80 files under tests/
ruff check apps shared tests                # the lint CI runs
shellcheck scripts/*.sh                     # the shell gate CI runs

# The API, exactly as Cloud Run runs it, against the seeded corpus
uvicorn apps.search.api:app --host 127.0.0.1 --port 8000 &
curl -s localhost:8000/health
curl -s 'localhost:8000/articles?limit=3'
curl -s 'localhost:8000/evidence/ledger' | head -c 600
curl -s -X POST localhost:8000/extract/ttps -H 'content-type: application/json' \
     -d '{"links":["https://example.invalid/news/usb-exfiltration"]}'
# With ADMIN_API_TOKEN=secret in the environment the write endpoints must 401/403
# without the bearer; with it unset they are open (local-dev default).

# The pipeline over local files (no network): write RawArticle JSON lines to
# $RAW_ARTICLES_PATH and run the processing graph over them
python -m apps.aggregator process --force

# The static UI in a real browser (Chromium is installed in the image)
python scripts/ui_smoke_ci.py               # serves web/ and drives it headless
python -m http.server 5500 --directory web  # or browse it yourself at :5500
```

Useful entry points: `apps/search/api.py` (routes), `apps/search/service.py`
(index + aggregation), `apps/aggregator/__main__.py` (CLI), `apps/aggregator/
pipeline.py` and `process_pipeline.py` (ingest and processing),
`shared/agents/article_processor.py` (the LangGraph graph),
`shared/agents/summarize.py` (the enrichment call and its spend gate),
`shared/schemas/` (every stored record's contract).

## How you rate severity

- **Critical**: code execution on the API service or inside the refresh
  pipeline from anonymous input or from collected content; reading or
  exfiltrating secrets (`ADMIN_API_TOKEN`, `EXPORT_API_TOKEN`, LLM provider
  keys, CourtListener, PACER, Reddit or X credentials); bypassing
  `_require_admin_token` or `_require_export_token`.
- **High**: stored cross-site scripting — a string in a collected document
  or an LLM response that executes in `web/app.js` for other visitors;
  server-side request forgery from the API service (the `ingest_url`
  endpoints fetch URLs, and Cloud Run has a metadata server); reading files
  outside the corpus mount or writing anywhere but the `config/` prefix;
  a crafted document that rewrites or deletes existing enrichment history
  (the append-only contract) or lets one document's text alter another
  row's stored record.
- **Medium**: denial of service that a single anonymous request or a
  single collected document can cause — a pathological regex, unbounded
  memory in a parser, a PDF or parquet file that hangs a cycle past its
  timeout; prompt injection that changes a stored verdict or analyst note
  as text only (integrity without execution); rate-limiter bypass on
  `/extract/ttps`.
- **Low**: anything that needs the local-dev configuration (admin token
  unset, open CORS origin list) to matter; findings in operator-side
  scripts that require the operator to already be compromised.

Reports and patches: a report should name the file and function, give a
request or input that reproduces it, and say which tier above it falls in.
Patches should be small, against `main`, pass `ruff` and `pytest`, and add a
test under `tests/` in the existing style. Prose in a patch (comments, UI
strings) follows the house voice in `CLAUDE.md`: short plain sentences,
no marketing words.

## Anything to leave alone

- **The rate limiter** (`apps/search/ratelimit.py`) is a CPU and abuse
  guard, not a security boundary. Do not report that it can be worked
  around by changing IPs.
- **The open-when-unset token gates** are the documented local-dev default.
  Report a bypass when the token is set, not the absence of a token.
- **Cold starts.** Cloud Run scales to zero on purpose; a slow first
  request is not a finding.
- **The LLM providers' own behaviour** (refusals, hallucinated fields) is
  handled by schema validation and select-best projection; a model that
  returns bad JSON is expected input, not a vulnerability, unless the
  parser does something unsafe with it.
- **Checked-in data** under `shared/data/` and `tests/fixtures/` is
  authored. `.secrets.baseline` holds `detect-secrets` hashes, not secrets.
- **Privacy invariants are design, not bugs**: the product reports roles,
  never individuals; it never resolves entities across cases; email local
  parts are redacted at a single choke point. Do not propose features that
  would identify people.
- **`scripts/deploy_cloud_run.sh` and `scripts/gcp_sweep_vm.sh`** are legacy
  fallbacks kept for rollback; findings there are low priority.
