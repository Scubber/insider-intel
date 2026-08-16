# Spark corpus-tenant cutover — session handoff (2026-08-16)

**For the next agent (Claude).** This is the live ops log of the 2026-08-16
Cursor session that stood up the DGX Spark as the ingest/enrichment tenant.
Architecture is already merged (`docs/dgx-spark.md` §4, sparky
`docs/insider-intel-integration.md`, PR #188). This file is *what was
actually done on the box*, what broke, and what is still unsafe to leave
running.

**URGENT:** a staging `spark_refresh.sh` is **still running** on sparky
(started 11:48Z, PIDs `410895`/`410908`, container
`insider-intel-spark-refresh-run-09d1dfb7399b`). Chain is
`sparky,anthropic`. vLLM times out at 300s on rich filings and **falls
through to Haiku**, spending Claude credits. Operator is low on credits.
**Kill it first** (see §Next). Cloud Scheduler is still the prod writer;
this job only writes `spark-staging/` after a successful pipeline — it has
not reached `push` yet, so prod GCS/site are untouched. The credit bleed
is Anthropic during enrich, not a prod corpus write.

---

## Topology (already decided — do not reopen)

```
sparky cron (not enabled yet):
  pull GCS → docker compose -f docker-compose.spark.yml run --rm refresh
    (python -m apps.aggregator all, vLLM first)
  → push GCS → POST /reload
website (Cloud Run + Pages): untouched; serves the bucket
```

Outbound only. No tunnel from GCP into the house. Pause Cloud Scheduler
`corpus-refresh-schedule` only after a proven cycle against the **real**
prefix, so the bucket has a single writer.

Host: NVIDIA DGX Spark, SSH alias **`sparky`** (Windows NVIDIA Sync →
`spark-85b2.local`, user `wintermute`). WSL has **no** SSH config for this
host — `scp`/`ssh sparky` must run from **Windows**, not WSL.

vLLM (live, do not assume the example SKU):

- Container `sparky-vllm`, docker network `sparky`, `http://vllm:8000/v1`
  (host `127.0.0.1:8001`)
- Served id: **`Qwen/Qwen3.8-27B-FP8`** (dense 27B). Spec example used to
  pin `nvidia/Qwen3.6-35B-A3B-NVFP4` — that is stale. PR #189 makes tagging
  follow the server.
- `--max-num-seqs 4`, `--gpu-memory-utilization 0.5`, `--reasoning-parser qwen3`
- Decode ~**15 tok/s** for a single JSON-mode enrichment; KV cache ~4%;
  waiting=0 during the staging job (Open WebUI idle)

---

## Done (this session)

### GCP IAM (from WSL `gcloud`, account `timothycarreira@gmail.com`)

- Verified `gs://insider-intel-502413-corpus` has **no** `allUsers` /
  `allAuthenticatedUsers`. Bindings: project legacy roles; `github-deployer`
  + `ingest-job` unconditional `objectAdmin`; `api-runtime` `objectViewer` +
  conditional `objectAdmin` on `config/`.
- Created SA `spark-corpus@insider-intel-502413.iam.gserviceaccount.com`
- Granted `roles/storage.objectAdmin` on the corpus bucket
  (`--condition=None` required because of the existing config/ condition)
- JSON key (do not commit, do not cat):
  - Minted at WSL `/home/wintermute/.config/insider-intel-spark-sa.json` (`0600`)
  - Copied to sparky `/home/wintermute/.config/insider-intel-spark-sa.json` (`0600`)
  - Windows staging copy was deleted after scp
- `gcloud config set project` as the Spark SA warns (Resource Manager API);
  ignore — the SA only needs objectAdmin on this bucket

### On sparky

- User-space Cloud SDK `~/google-cloud-sdk` (no sudo). PATH appended to
  `~/.bashrc`.
- `gcloud auth activate-service-account --key-file=…spark-sa.json`
- Smoke test (three commands):
  1. `gcloud storage ls gs://insider-intel-502413-corpus` — prefixes listed
  2. `gcloud storage ls …/processed/articles.jsonl` — object exists
  3. wrote `gs://…/spark-staging/smoke.txt` and cat'd it back
- `~/sparky` fast-forwarded to `9dd244c` (PR #2 integration doc). Local
  dirty files left in place (Caddyfile, compose, ACL, `scripts/push-acl.sh`)
  — **do not discard**.
- Cloned `~/insider-intel` at **`e071090`** (#188). Has **not** pulled PR #189.
- `.env.spark` (`0600`, gitignored) filled from Secret Manager
  (`ADMIN_API_TOKEN`, `ANTHROPIC_API_KEY`, `COURTLISTENER_API_TOKEN`) +
  `VLLM_API_KEY` from `~/sparky/.env`. Non-secret knobs:

  | Knob | Value on the box now |
  |---|---|
  | `SPARK_CORPUS_BUCKET` | `gs://insider-intel-502413-corpus/spark-staging` |
  | `SUMMARIZER_LLM_PROVIDER` | `sparky,anthropic` ← **credit leak** |
  | `OPENAI_COMPAT_TIMEOUT_SECONDS` | `300` ← too short (see below) |
  | `SUMMARIZER_MAX_ARTICLES_PER_RUN` | `200` (copied from example; prod is 25) |
  | `LLM_CUSTOM_PROVIDERS` model | `Qwen/Qwen3.8-27B-FP8` (live served id) |
  | `GOOGLE_APPLICATION_CREDENTIALS` | `/home/wintermute/.config/insider-intel-spark-sa.json` |
  | `SPARK_RELOAD_URL` | `https://api.intel.thederpweb.com/reload` |

  No `OPENAI_API_KEY` (retarget landmine). No PACER secrets (staging must
  not spend PACER). No Reddit OAuth (API ingest 403'd).
- Seeded `spark-staging/{raw,processed,state,config}` from prod prefixes
  (copy, not a cutover).
- Started `scripts/spark_refresh.sh` at **11:47Z** with
  `PATH=$HOME/google-cloud-sdk/bin:$PATH`, log
  `/tmp/spark_refresh_staging.log`. Reached **pipeline**; **not** push/reload.

### Code / PRs

| PR | State | What |
|---|---|---|
| Scubber/insider-intel#188 | **merged** | tenant files: `docker-compose.spark.yml`, `.env.spark.example`, `scripts/spark_refresh.sh`, timeout knob |
| Scubber/sparky#2 | **merged** | `docs/insider-intel-integration.md` (still quotes Qwen 3.6 in examples) |
| Scubber/insider-intel#189 | **OPEN** | stamp `forensics.model` from completion `model`; `model: auto` probes `GET /v1/models`; example no longer pins a Qwen SKU. Branch `spark/dynamic-model-tag`, commit `656f838`. Tests: 49 passed (`test_llm_chain` + `test_summarize`). WSL worktree: `~/insider-intel-dynamic-model` |

Windows Cursor workspace was moved to that worktree. Canonical WSL
`~/insider-intel` is still `dev/toolbox-container` with unrelated dirty
files — **do not mix**.

---

## vLLM timeouts (root cause — measured)

Not queue, not OOM, not a missing timeout, not Open WebUI.

- Container **has** `OPENAI_COMPAT_TIMEOUT_SECONDS=300`
- Client is **non-streaming** `httpx.post(..., timeout=300)` → e2e generation
  deadline, no bytes until vLLM finishes
- Enrichment `max_tokens=12000`; Qwen3 `--reasoning-parser qwen3` burns
  thinking tokens on the same clock
- Observed **~15 tok/s** → 12k tokens ≈ **800s**; 300s ≈ 4.4k tokens
- Log pattern: `methods=0` cases finish in 100–270s on vLLM; richer ones
  fail at **exactly 301s** then Anthropic. Count at last snapshot:
  **4 timeouts, 4 `api.anthropic.com` 200s**
- First Anthropic success worth reading: **Nasir v. Equinix Inc**
  (W.D. Wash. `2:26-cv-02685`, insider=False, conf 0.85, methods=7). JSONL
  not flushed (pipeline still in memory); site unchanged.

Intended fix (started, **not landed** — kill interrupted):

1. Kill the running refresh (stop credit spend **now**)
2. `.env.spark`: `OPENAI_COMPAT_TIMEOUT_SECONDS=900` (12k @ 15 tok/s fits)
3. `.env.spark`: `SUMMARIZER_LLM_PROVIDER=sparky` (no Anthropic until credits recover)
4. Code: `OPENAI_COMPAT_ENABLE_THINKING=false` → vLLM
   `chat_template_kwargs.enable_thinking=false` so thinking tokens do not
   eat the budget. Default true so Cloud Run openai-compat (gpt-4o) is unchanged.
5. Lower `SUMMARIZER_MAX_ARTICLES_PER_RUN` for the first proving run (200 at
   ~2–15 min/article is many hours). Prod trickle is 25.

---

## What has NOT been done (cutover checklist)

From `docs/dgx-spark.md` §4. Do in this order:

1. **Stop the current staging job** (urgent — credits)
2. Retune `.env.spark` (timeout 900, no anthropic, optional thinking-off after code)
3. Rebuild Spark image if code changed (`docker compose -f docker-compose.spark.yml build`)
4. Re-run `scripts/spark_refresh.sh` against **`spark-staging`** until
   `spark_refresh: done`. Confirm staging `processed/articles.jsonl` grew and
   `forensics.model` is the served Qwen id (or Haiku only if fallback still on)
5. **Do not** treat a staging `/reload` as “the site shows Spark rows” — Cloud
   Run reads prod prefixes, not `spark-staging/`
6. Point `SPARK_CORPUS_BUCKET` at `gs://insider-intel-502413-corpus` (no suffix)
7. Pause Cloud Scheduler job `corpus-refresh-schedule` (us-east1)
8. Enable a 6h timer/cron on sparky matching `0 */6 ET` =
   `0 4,10,16,22 * * *` UTC in August. No unit file exists in either repo yet.
   User crontab is easier than systemd --user (no linger/sudo). Wrapper must
   export `PATH=$HOME/google-cloud-sdk/bin:$PATH`
9. Merge #189 before relying on `model: auto` / response tagging
10. Update sparky `docs/insider-intel-integration.md` so it no longer pins
    Qwen 3.6 (second repo; local dirty tree on `~/sparky`)
11. Reddit OAuth if the Spark residential-IP win is wanted (this run 403'd
    without `REDDIT_CLIENT_ID`/`SECRET`)
12. PACER secrets only when taking over **prod** writes, never on staging

**Prod is still:** Cloud Run job `corpus-refresh` every 6h, Haiku trickle
(25/run), last prod `processed/articles.jsonl` write **2026-08-16T10:17:40Z**,
API `last_indexed_at=2026-08-16T10:08:39Z`, `indexed_articles=7174`.
No Spark rows on intel.thederpweb.com.

---

## Commands (copy-paste)

Kill the credit bleed (Windows, not WSL):

```bash
ssh sparky 'docker kill $(docker ps -q --filter name=insider-intel-spark-refresh) ; pkill -f scripts/spark_refresh.sh ; pgrep -af spark_refresh || echo stopped'
```

Then on sparky, retune env (python, do not echo secrets) and rerun:

```bash
export PATH="$HOME/google-cloud-sdk/bin:$PATH"
cd ~/insider-intel
# edit .env.spark: TIMEOUT=900, SUMMARIZER_LLM_PROVIDER=sparky
nohup bash scripts/spark_refresh.sh > /tmp/spark_refresh_staging.log 2>&1 &
```

`gsutil` is in the user-space SDK; non-interactive SSH does not load
`~/.bashrc` unless you `bash -lc` or export PATH.

---

## Secrets / files not to commit

- `~/.config/insider-intel-spark-sa.json` (WSL and sparky)
- `~/insider-intel/.env.spark`
- `~/sparky/.env` (`VLLM_API_KEY`)
- docker inspect of `sparky-vllm` prints the API key in `Cmd` — do not paste
