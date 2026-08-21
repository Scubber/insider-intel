# DGX Spark as a dev + local-LLM box

How to use an NVIDIA DGX Spark (GB10, 128GB unified memory, ARM64) with this
repo: as a Cursor remote dev machine, and as a local LLM endpoint for the
enrichment chain in **local dev**. Sections 1–3 never touch production; §4
(the corpus tenant) is production's data path — **LIVE since the 2026-08-16
cutover** (operating state at the end of §4).

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
  `OpenAI-compat chat call failed` and falls through the chain. The client is
  a **non-streaming POST**, so `OPENAI_COMPAT_TIMEOUT_SECONDS` is an
  end-to-end *generation deadline*: size it as `ENRICH_MAX_TOKENS (12,000) ÷
  your measured decode tok/s + headroom` — e.g. ~15 tok/s ⇒ ~800s ⇒ set 900.
  Undersizing it doesn't fail loudly; it silently partitions work so the local
  box completes only thin cases while every rich case times out at exactly the
  deadline (and bills the fallback, if one is chained — the 2026-08-16
  incident).
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
  LLM chain is the named custom provider (`sparky`, $0) **alone** by default:
  a local failure then costs a slot (re-swept free next cycle), never money.
  Chaining a funded fallback is a deliberate opt-in — combined with raised
  caps, one slow/dead vLLM converts the whole raised budget into paid calls.
  Set `model` to `auto` (the example default) so the job takes whatever vLLM
  is serving; do not pin a Qwen SKU in this repo.
- `scripts/spark_refresh.sh` — one cycle: bucket pull → pipeline → bucket
  push → `/reload` (skipped while `SPARK_RELOAD_URL` is empty, i.e. on
  staging). Cron/systemd-timer it (e.g. every 6h) from the repo root. The
  script exports the user-space Cloud SDK onto `PATH` itself (cron and
  non-interactive SSH never read `~/.bashrc`), holds a `flock` so cycles
  never overlap (skips are logged, not silent), bounds a cycle at 5.5h, and
  passes `--build` so a `git pull` is actually picked up — plain
  `compose run` reuses a stale image forever. It pushes only
  `raw/ processed/ state/`: `config/` is pull-only because the prod API
  itself writes subscriptions there.

**Cutover order matters — the scheduler pause comes before any real-prefix
run:**

1. Prove a full manual cycle against the `spark-staging/` bucket prefix
   (`SPARK_RELOAD_URL` empty).
2. **Pause the Cloud Scheduler refresh job.** Both writers whole-file-replace
   `processed/articles.jsonl`; append-only history and billed-once dedup are
   row-level properties *inside* the file and give zero protection against
   object replacement, so a Spark push racing a cloud-job write silently
   deletes the loser's rows (recoverable only via bucket versioning +
   `corpus-recover.yml`, and only if noticed).
3. Only now point `SPARK_CORPUS_BUCKET` at the bucket root, set
   `SPARK_RELOAD_URL`, and run one watched real-prefix cycle.
4. Enable the timer (user crontab is simplest; the script provides its own
   PATH and locking).

The paused cloud job stays around as a manual fallback; enrichment provenance
is per-record (`forensics.model` = the id the server put on the completion,
e.g. whatever Qwen SKU is loaded), so a mixed local/Claude corpus is normal
and select-best over `enrichment_history` keeps the richer record either way.

Residential-IP bonus: the Reddit lane, which 429s from cloud IPs, works from
the Spark.

### Operating state (cutover complete 2026-08-16)

The steps above were executed 2026-08-16; the Spark is the production
refresh tenant. What is running now:

- **Cron** (sparky user crontab): `0 8 * * *` UTC with the
  `PATH=$HOME/google-cloud-sdk/bin:$PATH` prefix, logging to
  `~/insider-intel/logs/spark_refresh.log`. First unattended cycle
  2026-08-17 00:00→00:31:51Z verified end-to-end (corpus 7,193 rows /
  1,839 enriched, API reloaded).
- **Caps**: `SUMMARIZER_MAX_ARTICLES_PER_RUN=40`,
  `SUMMARIZER_BACKFILL_RESERVE=30` — operator-approved raise from the cloud
  trickle's 25/15, affordable because the chain is $0.
- **Chain**: `SUMMARIZER_LLM_PROVIDER=sparky` alone (local
  `Qwen/Qwen3.8-27B-FP8` via vLLM, `model: auto` probe,
  `OPENAI_COMPAT_TIMEOUT_SECONDS=900`); `forensics.model` is stamped from
  each completion. $0 LLM spend, 0 Anthropic calls.
- **PACER**: creds live in `.env.spark` on sparky (moved at cutover);
  purchases stay capped at 5/run.
- **Rollback**: `crontab -r` on sparky, resume Cloud Scheduler
  `corpus-refresh-schedule` (paused 2026-08-16 ~19:45Z, retained),
  optionally `gcloud run jobs execute corpus-refresh` once.
- **Known-accepted issues**: hunt synthesis fails under Qwen thinking mode
  (fix = `OPENAI_COMPAT_ENABLE_THINKING` knob, merged; activation gated on a
  gold-set A/B); occasional Qwen JSON parse failures (guided-JSON work
  queued); vLLM API key rotation scheduled for the next restart window.
