# DGX Spark as a dev + local-LLM box

How to use an NVIDIA DGX Spark (GB10, 128GB unified memory, ARM64) with this
repo: as a Cursor remote dev machine, and as a local LLM endpoint for the
enrichment chain in **local dev**. Nothing here touches production.

**Security ground rules (this repo is public):**

- Never commit hostnames, IPs, Tailscale names, tunnel URLs, or keys. Real
  values live in `.env` (gitignored; `detect-secrets` guards commits) or your
  SSH config — this doc uses placeholders only.
- Never point the prod Cloud Run job or service at a home-network endpoint.
  Prod's LLM chain is configured in `deploy-api.yml` and stays on funded
  API providers.
- The Spark serves models on your LAN/tailnet only — don't expose the
  OpenAI-compatible port to the internet.

## 1. Cursor → Spark (remote development)

The Spark runs DGX OS (Ubuntu-based, **aarch64**). Use Cursor's Remote-SSH:

1. Reachability: put the Spark and your laptop on a tailnet (or LAN) and add
   an entry to `~/.ssh/config` — key auth only, disable password login on
   the Spark:

   ```
   Host spark
     HostName <spark-ip-or-tailnet-name>
     User <you>
     IdentityFile ~/.ssh/<key>
   ```

2. Cursor → "Connect to Host…" → `spark`, open the repo checkout there.
   Cursor's agent picks up the repo manual automatically: `AGENTS.md` and
   `.cursor/rules/agent-manual.mdc` already point at `CLAUDE.md` and
   `docs/HANDOFF.md`.
3. Dev stack on the Spark: `make up` as usual (API :8000, UI :5500).
   ARM64 notes: the dev image is plain Python and builds fine on aarch64;
   install `docker-buildx` (heredoc Dockerfile needs BuildKit); Playwright's
   Chromium has arm64 builds. Everything in docs/DEVELOPMENT.md applies,
   minus the WSL2-specific parts.

## 2. Spark as the local enrichment LLM (dev only)

The provider chain's `openai` entry **is** the local-model path — it means
"any OpenAI-compatible endpoint" and already defaults to a local server
(`http://localhost:11434/v1`, Ollama). 128GB of unified memory comfortably
serves large open-weight models.

Serve a model (either works; both speak `/v1/chat/completions`):

- **Ollama** (simplest): `ollama serve` + `ollama pull <model>` — matches the
  repo defaults out of the box.
- **vLLM** (faster batch throughput, better for enrichment sweeps):
  `vllm serve <model> --port 8001` (use NVIDIA's ARM64/GB10 container).

Wire local dev to it in `.env` on the machine running the stack:

```dotenv
# Enrichment (analyst note + forensics) on the Spark:
SUMMARIZER_LLM_PROVIDER=openai
OPENAI_COMPAT_BASE_URL=http://<spark>:11434/v1   # or :8001/v1 for vLLM
OPENAI_COMPAT_MODEL=<served-model-name>
# Optional: the social-post classifier refiner on the same endpoint
CLASSIFIER_LLM_PROVIDER=openai
```

Gotchas (from `shared/llm/`):

- **Don't set a bare `OPENAI_API_KEY`** in the same env unless you also set
  `OPENAI_COMPAT_BASE_URL` explicitly — a bare key retargets the `openai`
  provider to real OpenAI (`gpt-4o-mini`) when base/model are still defaults.
- The client requests strict JSON mode and auto-retries without it if the
  server 400s — llama.cpp/older servers work, but prefer a server with JSON
  mode for cleaner forensics extraction.
- `SUMMARIZER_MODEL` overrides only the **first** chain entry; in a
  local-only chain (`openai` alone) set `OPENAI_COMPAT_MODEL` instead.
- A local model generating the full enrichment JSON (up to 12k output tokens)
  can outlast the client's 90s default timeout, which logs
  `OpenAI-compat chat call failed` and falls through the chain. Raise
  `OPENAI_COMPAT_TIMEOUT_SECONDS` (e.g. 300) on the box serving the model.
- Alternatively, define a named entry in `LLM_CUSTOM_PROVIDERS`
  (`{"spark": {"base_url": "http://<spark>:8001/v1", "model": "auto"}}`)
  and reference `spark` in the chain — `model: auto` probes `GET /v1/models`.
  Same mechanics, and it can sit as a fallback after funded providers. Custom
  entries work in the summarizer / discoverer / hunt-synthesis chains (not
  the classifier). `forensics.model` is stamped from the completion
  response's `model` field, so a host weight swap shows up in provenance
  without a tenant config edit.

Then exercise it: `make shell` → seed a corpus (docs/DEVELOPMENT.md) →
`python -m apps.aggregator process --force` and watch `Case enriched …`
lines come from your own silicon.

## 3. What the Spark can NOT do (today)

**There is no import lane for offline-produced enrichments.** The
`export-llm` workflow ships NDJSON *out* (bucket `export/` prefix) for
off-site analysis — great Spark fodder for research passes over the corpus —
but nothing reads enrichment output back in: enrichments are written only by
the corpus-refresh job's enrich node, `enrichment_history` is append-only,
and the API's bucket write access is limited to `config/`. Turning the Spark
into a *partial* enricher would be a deliberate design change (a guarded
import lane), not a config flip — parked with open thread #5 in
docs/HANDOFF.md. The sanctioned alternative is §4: the Spark takes over the
**whole** refresh job, which needs no import lane at all.

## 4. Spark as the corpus tenant (the whole refresh job on the Spark)

The supported way to put the Spark in production's data path: it runs the
entire scheduled ingest + enrichment cycle and syncs the corpus with the
private GCS bucket; the Cloud Run service and the public site are untouched
and keep serving whatever is in the bucket. This does not violate the ground
rules above — prod never dials a home endpoint; the Spark only makes
*outbound* calls (GCS, the `/reload` poke, and the ingestion sources), and
its own LLM traffic stays on the local docker network.

Files:

- `docker-compose.spark.yml` — the refresh job as a tenant of the "sparky"
  host pattern: joins the external `sparky` docker network, reaches vLLM at
  `http://vllm:8000/v1`, publishes no ports.
- `.env.spark.example` — copy to `.env.spark` (gitignored) and fill in. The
  LLM chain is a named custom provider first (`sparky`, $0) with a funded
  fallback second, so a local timeout costs money, not correctness. Set
  `model` to `auto` (the example default) so the job takes whatever vLLM is
  serving; do not pin a Qwen SKU in this repo.
- `scripts/spark_refresh.sh` — one cycle: bucket pull → pipeline → bucket
  push → `/reload`. Cron/systemd-timer it (e.g. every 6h) from the repo root.

Cutover order matters: prove a full manual cycle first (ideally against a
staging bucket prefix), **pause the Cloud Scheduler refresh job** so the
bucket has a single writer, then enable the timer. The paused cloud job stays
around as a manual fallback; enrichment provenance is per-record
(`forensics.model` = the id the server put on the completion, e.g. whatever
Qwen SKU is loaded), so a mixed local/Claude corpus is normal and select-best
over `enrichment_history` keeps the richer record either way.

Residential-IP bonus: the Reddit lane, which 429s from cloud IPs, works from
the Spark.
