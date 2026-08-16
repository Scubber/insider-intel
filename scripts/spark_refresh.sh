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

echo "spark_refresh: pull $(date -u +%FT%TZ)"
for p in "${PULL_PREFIXES[@]}"; do
  mkdir -p "data/${p}"
  gsutil -m rsync -r "${SPARK_CORPUS_BUCKET}/${p}" "data/${p}" || [ "${p}" = "config" ]
done

echo "spark_refresh: pipeline $(date -u +%FT%TZ) code $(git rev-parse --short HEAD)"
# --build: `compose run` reuses an existing image and never rebuilds, so a git
# pull is inert without it (layer cache makes the no-change case cheap).
# timeout: bound a cycle at 5.5h — inside the 6h cadence — so a wedged vLLM
# can't hold the flock across every subsequent tick.
timeout --signal=INT 19800 docker compose -f docker-compose.spark.yml run --build --rm refresh

echo "spark_refresh: push"
for p in "${PUSH_PREFIXES[@]}"; do
  gsutil -m rsync -r "data/${p}" "${SPARK_CORPUS_BUCKET}/${p}"
done

if [ -n "${SPARK_RELOAD_URL:-}" ]; then
  echo "spark_refresh: reload"
  curl -fsS -X POST -H "Authorization: Bearer ${ADMIN_API_TOKEN}" "${SPARK_RELOAD_URL}"
  echo ""
else
  echo "spark_refresh: reload skipped (SPARK_RELOAD_URL empty)"
fi
echo "spark_refresh: done $(date -u +%FT%TZ)"
