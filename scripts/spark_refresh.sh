#!/usr/bin/env bash
# One corpus refresh cycle on a DGX Spark tenant box (docs/dgx-spark.md §4):
#
#   pull corpus from the private GCS bucket
#     → run the full pipeline against local vLLM (docker-compose.spark.yml)
#     → push the updated corpus back
#     → poke the Cloud Run API to reload (skipped when SPARK_RELOAD_URL is empty)
#
# Run from the repo root via cron/systemd-timer (e.g. every 6h). Pause the
# Cloud Scheduler refresh job BEFORE any cycle against the real prefix — two
# writers on one bucket will race, and the loser's paid enrichments are
# silently replaced. Config comes from .env.spark (see .env.spark.example).
# All traffic is outbound; nothing on the Spark is exposed.
set -euo pipefail
cd "$(dirname "$0")/.."

# Cron and non-interactive SSH never load ~/.bashrc, and the Cloud SDK is a
# user-space install — without this line every unattended cycle dies at the
# first pull (exit 127) with nothing in the log to say why.
export PATH="$HOME/google-cloud-sdk/bin:$PATH"
command -v gsutil >/dev/null || { echo "spark_refresh: gsutil not on PATH" >&2; exit 2; }

# Never overlap two cycles (a drain run can outlast the cron interval). The
# skip must be loud: a wedged cycle otherwise starves every later tick and the
# corpus goes stale with no trace.
exec 9>/tmp/insider-intel-spark-refresh.lock
flock -n 9 || {
  echo "spark_refresh: $(date -u +%FT%TZ) previous cycle still holds the lock, skipping" >&2
  exit 0
}

set -a
# shellcheck source=/dev/null
source .env.spark
set +a
: "${SPARK_CORPUS_BUCKET:?set in .env.spark}"
# SPARK_RELOAD_URL may be empty on purpose: a staging-prefix rehearsal writes
# nothing the API serves, so poking /reload (an OOM-fatal index swap on the
# prod service) would be pure risk. Set it only when the bucket is the prod root.
if [ -n "${SPARK_RELOAD_URL:-}" ]; then
  : "${ADMIN_API_TOKEN:?set in .env.spark when SPARK_RELOAD_URL is set}"
fi

# Bucket root == /app/data layout. Sync only the prefixes the pipeline owns —
# never export/ (workflow-published) — and never with delete flags: additive
# rsync means a partial failure can't destroy bucket state.
# config/ is pull-only: it is the ONE prefix the prod API itself writes
# (subscription writes — the api-runtime config/ IAM condition in CLAUDE.md
# exists for exactly this), so pushing it back would revert any subscription
# added while this cycle ran.
PULL_PREFIXES=(raw processed state config)
PUSH_PREFIXES=(raw processed state)

# The Spark is a chat host between cycles: compose.yml's vllm command serves
# whatever model the operator talks to. The cycle BORROWS the box for the
# enrichment model and hands it back on the way out. The swap is sequential by
# design — two models of this size cannot co-reside on 128GB of unified memory
# (two crashes on 2026-08-20 taught us the binding limit is the load-time peak).
#
# No override file present => this whole block is inert and the cycle enriches
# with whatever is already loaded.
SPARKY_COMPOSE_DIR="${SPARKY_COMPOSE_DIR:-$HOME/sparky}"
SPARKY_ENRICH_OVERRIDE="${SPARKY_ENRICH_OVERRIDE:-model-enrich.yml}"
SPARKY_MODEL_URL="${SPARKY_MODEL_URL:-http://127.0.0.1:8001/v1/models}"
SPARKY_MODEL_WAIT_SECONDS="${SPARKY_MODEL_WAIT_SECONDS:-2400}"
# Restored together on the way out: open-webui reads OPENAI_API_KEY from
# compose at container-create time, so recreating only vllm would leave the UI
# holding a stale key after any rotation.
SPARKY_CHAT_SERVICES="${SPARKY_CHAT_SERVICES:-vllm open-webui}"
# What the hand-back serves. Empty = base compose.yml's model. Set to an
# overlay file in ~/sparky (e.g. model-enrich.yml for Nemotron) in .env.spark
# to make that the STANDING chat default without editing compose.yml —
# operator choice 2026-08-24: default is the Nemotron enrich overlay.
SPARKY_CHAT_OVERRIDE="${SPARKY_CHAT_OVERRIDE:-}"

vllm_up() { # $1: override file (empty for base) ; $2...: services
  local files=(-f compose.yml)
  [ -n "${1:-}" ] && files+=(-f "$1")
  shift || true
  local services=("$@")
  [ "${#services[@]}" -eq 0 ] && services=(vllm)
  (cd "$SPARKY_COMPOSE_DIR" && docker compose "${files[@]}" up -d --no-deps "${services[@]}")
}

vllm_wait() { # 0 once /v1/models answers (prints the served id), 1 on timeout
  local key deadline=$((SECONDS + SPARKY_MODEL_WAIT_SECONDS))
  key=$(sed -n 's/^VLLM_API_KEY=//p' "$SPARKY_COMPOSE_DIR/.env" | head -1)
  while [ "$SECONDS" -lt "$deadline" ]; do
    if curl -fsS -H "Authorization: Bearer ${key}" "$SPARKY_MODEL_URL" 2>/dev/null |
      grep -o '"id":"[^"]*"' | head -1; then
      return 0
    fi
    sleep 15
  done
  return 1
}

restore_chat_model() {
  local override="$SPARKY_CHAT_OVERRIDE"
  if [ -n "$override" ] && [ ! -f "$SPARKY_COMPOSE_DIR/$override" ]; then
    echo "spark_refresh: chat override $override missing — restoring base model" >&2
    override=""
  fi
  echo "spark_refresh: restoring chat model${override:+ ($override)}"
  # Deliberate word-split: SPARKY_CHAT_SERVICES is a service-name list.
  # shellcheck disable=SC2086
  vllm_up "$override" $SPARKY_CHAT_SERVICES ||
    echo "spark_refresh: [FAIL] chat stack not restored" >&2
}

SWAPPED_MODEL=0
if [ -f "$SPARKY_COMPOSE_DIR/$SPARKY_ENRICH_OVERRIDE" ]; then
  echo "spark_refresh: loading enrichment model ($SPARKY_ENRICH_OVERRIDE)"
  # Arm the restore FIRST: a failed pull, a timed-out pipeline, or a kill must
  # never leave the box parked on the enrichment model.
  trap restore_chat_model EXIT
  SWAPPED_MODEL=1
  vllm_up "$SPARKY_ENRICH_OVERRIDE"
else
  echo "spark_refresh: no $SPARKY_ENRICH_OVERRIDE — enriching with the loaded model"
fi

echo "spark_refresh: pull $(date -u +%FT%TZ)"
for p in "${PULL_PREFIXES[@]}"; do
  mkdir -p "data/${p}"
  gsutil -m rsync -r "${SPARK_CORPUS_BUCKET}/${p}" "data/${p}" || [ "${p}" = "config" ]
done
# Bulk-sweep spool: when the sweep runs on a cloud VM its match chunks land
# under this prefix; pull them so the pipeline's spool merge ingests them.
# Empty/absent prefix is a no-op; local-sweep chunks are already in place.
mkdir -p data/raw/sweep_spool
gsutil -m rsync "${SPARK_CORPUS_BUCKET}/raw/sweeps/indiacourts" data/raw/sweep_spool \
  2>/dev/null || true

if [ "$SWAPPED_MODEL" = "1" ]; then
  # Weights load while the corpus pulls; block only now, right before the run.
  vllm_wait || echo "spark_refresh: [FAIL] enrichment model never served — \
ingest and docket-follow still run, enrichment will no-op" >&2
fi

echo "spark_refresh: pipeline $(date -u +%FT%TZ) code $(git rev-parse --short HEAD)"
# --build: `compose run` reuses an existing image and never rebuilds, so a git
# pull is inert without it (layer cache makes the no-change case cheap).
# timeout: bound the pipeline at 8h — well inside the daily cadence — so a
# wedged vLLM can't hold the flock across the next tick. (Was 5.5h when the
# cadence was 6h; the cycle now also spends ~30min loading the model.)
timeout --signal=INT 28800 docker compose -f docker-compose.spark.yml run --build --rm refresh

echo "spark_refresh: push"
for p in "${PUSH_PREFIXES[@]}"; do
  gsutil -m rsync -r "data/${p}" "${SPARK_CORPUS_BUCKET}/${p}"
done

# Retire bucket-spool chunks the pipeline merged this cycle (they moved to
# ingested/ locally); otherwise every pull re-downloads them. Re-merging is
# harmless (link-dedupe) so a failure here is cosmetic.
if compgen -G "data/raw/sweep_spool/ingested/*.jsonl" > /dev/null 2>&1; then
  for f in data/raw/sweep_spool/ingested/*.jsonl; do
    gsutil rm "${SPARK_CORPUS_BUCKET}/raw/sweeps/indiacourts/$(basename "$f")" \
      2>/dev/null || true
  done
fi

if [ -n "${SPARK_RELOAD_URL:-}" ]; then
  echo "spark_refresh: reload"
  # The bucket push above is the deliverable; /reload only shortens the wait
  # until a running instance serves it (a replaced instance boots from the
  # bucket anyway). So: bounded, retried, and NEVER fatal — a 503 here (the
  # index-swap memory spike, CLAUDE.md gotchas) must not fail a cycle whose
  # enrichment already landed (2026-09-05: two false-red cycles after the
  # field-backfill drain).
  if curl -fsS --max-time 300 --retry 2 --retry-delay 45 --retry-all-errors \
      -X POST -H "Authorization: Bearer ${ADMIN_API_TOKEN}" "${SPARK_RELOAD_URL}"; then
    echo ""
  else
    echo "[WARN] spark_refresh: reload returned non-2xx (curl exit $?) — bucket push already complete; the service serves it from its next instance start"
  fi
else
  echo "spark_refresh: reload skipped (SPARK_RELOAD_URL empty)"
fi
echo "spark_refresh: done $(date -u +%FT%TZ)"
