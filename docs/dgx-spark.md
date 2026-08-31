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
  is serving; do not pin a model id in this repo (the enrichment model is
  chosen on sparky — Nemotron 3 Super going forward).
- `scripts/spark_refresh.sh` — one cycle: load the enrichment model
  (layer `model-enrich.yml`, EXIT trap armed FIRST so any exit restores the
  chat stack) → bucket pull → wait for the model to serve → pipeline →
  bucket push → `/reload` (skipped while `SPARK_RELOAD_URL` is empty, i.e.
  on staging) → chat stack restored by the trap. Cron it once daily from
  the repo root (production: `0 8 * * *` UTC). The script exports the
  user-space Cloud SDK onto `PATH` itself (cron and non-interactive SSH
  never read `~/.bashrc`), holds a `flock` so cycles never overlap (skips
  are logged, not silent), bounds the pipeline at 8h (`timeout 28800`), and
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
is per-record (`forensics.model` = the id the server put on the completion —
whatever model the server reports; Nemotron 3 Super in production), so a
mixed local/Claude corpus is normal
and select-best over `enrichment_history` keeps the richer record either way.

Residential-IP bonus: the Reddit lane, which 429s from cloud IPs, works from
the Spark.

### Operating state (cutover 2026-08-16; current as of 2026-08-31)

The Spark is the production refresh tenant. What is running now:

- **Cron** (sparky user crontab): one cycle daily, `0 8 * * *` UTC, with the
  `PATH=$HOME/google-cloud-sdk/bin:$PATH` prefix, logging to
  `~/insider-intel/logs/spark_refresh.log`. The Pages boot snapshot rebuilds
  at 08:40Z (`pages.yml`).
- **Model borrow/restore**: the box is a chat host between cycles
  (`~/sparky/compose.yml` serves whatever the operator talks to — chat only,
  never enrichment). The cycle layers `~/sparky/model-enrich.yml` to load
  the enrichment model — Nemotron 3 Super 120B-A12B-NVFP4 at gpu-util 0.70,
  131k ctx, 1 seq — and an EXIT trap in `spark_refresh.sh` restores the chat
  stack (vllm + open-webui together) on any exit.
- **The swap is skipped when it would change nothing** (2026-08-31). Before
  touching the container, `spark_refresh.sh` fingerprints the recipe it wants
  (`docker compose config`, image + command, hashed — the rendered command
  carries `--api-key`, so it is never printed raw) against the recipe the
  running container actually has (`docker inspect`). Identical means no
  recreate, no load, no trap, no restore. Since chat became the same Nemotron
  as enrichment (2026-08-22) that is the normal path. Set the standing chat
  model to the enrich overlay (`sparky-ops` → `chat-default`, overlay
  `model-enrich.yml`) and a whole cycle costs **zero** model loads instead of
  two.
- **Memory law** (three crashes taught it): the binding limit is the
  LOAD-TIME peak, not steady state. Hard ceiling ≈ 75GB of weights; never
  boot two big models side by side; no fastsafetensors. The third crash was
  the subtle one — a *recreate of the same model* is also two models briefly,
  because `compose up` starts the incoming load the moment the outgoing
  container is told to stop. So a swap that must happen now goes stop →
  drain → start: `vllm_drain` polls `/proc/meminfo` (nvidia-smi reports N/A
  for memory on GB10 unified memory) until `MemAvailable` clears
  `SPARKY_DRAIN_MIN_AVAIL_GB` (80) or `SPARKY_DRAIN_SECONDS` (300) runs out.
  On timeout the cycle refuses to load and leaves vLLM stopped — enrichment
  no-ops, ingest and docket-follow still run.
- **A model that never serves gets stopped, not retried.** `vllm_wait`
  failing now stops the container. Leaving a failed load to docker's restart
  policy is what froze the corpus for five days in August 2026 (see the
  crash-loop gotcha in CLAUDE.md), so `~/sparky`'s vllm service is
  `restart: on-failure:3`. The trade-off is that vLLM no longer auto-starts
  after a host reboot; the next daily cycle brings it back, because a stopped
  container fails the recipe check and takes the swap path.
- **Chain**: `SUMMARIZER_LLM_PROVIDER=sparky` alone (`model: auto` probe,
  `OPENAI_COMPAT_TIMEOUT_SECONDS=900`), prompt contract v3
  (docs/schema-freeze-v3.md), `OPENAI_COMPAT_GUIDED_JSON=0` for Nemotron
  (the 2026-08-22 control run measured guided decoding at −10 points of
  verdict accuracy). `forensics.model` is stamped from each completion.
  $0 LLM spend.
- **Pipeline bound**: `timeout 28800` (8h) around the run; the refresh
  holds `/tmp/insider-intel-spark-refresh.lock` so overlapping cycles skip
  loudly.
- **PACER**: creds live in `.env.spark` on sparky (moved at cutover);
  purchases stay capped at 5/run.
- **Rollback**: `crontab -r` on sparky, resume Cloud Scheduler
  `corpus-refresh-schedule` (paused 2026-08-16, retained), optionally
  `gcloud run jobs execute corpus-refresh` once. NEVER run the Cloud Run
  job while the sparky cron is live — two writers on one bucket race, and
  the loser's enrichments are silently replaced.

### Access + IAM record (moved from the retired cutover handoff)

- **SSH**: alias `sparky`, defined in the Windows profile's SSH config
  (NVIDIA Sync). WSL has no SSH config for this host — use Windows interop
  `ssh.exe sparky '<cmd>'` from WSL; plain WSL `ssh` is refused. Never
  commit the hostname or IPs.
- **GCS service account**:
  `spark-corpus@insider-intel-502413.iam.gserviceaccount.com`, granted
  `roles/storage.objectAdmin` on the corpus bucket (`--condition=None` was
  required because of the existing `config/` condition). JSON key at
  `~/.config/insider-intel-spark-sa.json` (0600) on WSL and sparky — never
  commit, never cat. Bucket has no `allUsers` bindings; `api-runtime` holds
  `objectViewer` + conditional `objectAdmin` on `config/` only.
- **Key hygiene**: `docker inspect sparky-vllm` prints the vLLM API key in
  `Cmd`, and the same key sits in the container's process argv (`ps aux`
  leaks it identically). Treat any inspect/ps paste as a disclosure and
  rotate: one shared value lives in `~/sparky/.env` (`VLLM_API_KEY`) and
  `~/insider-intel/.env.spark` (`SPARKY_API_KEY`); recreate vllm AND
  open-webui together after a rotation (open-webui bakes the key at
  container create). Verify by hash, never by printing.
