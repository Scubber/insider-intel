#!/usr/bin/env bash
# One corpus refresh cycle on a DGX Spark tenant box (docs/dgx-spark.md §4):
#
#   pull corpus from the private GCS bucket
#     → run the full pipeline against local vLLM (docker-compose.spark.yml)
#     → push the updated corpus back
#     → poke the Cloud Run API to reload
#
# Run from the repo root via cron/systemd-timer (e.g. every 6h). Pause the
# Cloud Scheduler refresh job first — two writers on one bucket will race.
# Config comes from .env.spark (see .env.spark.example). All traffic is
# outbound; nothing on the Spark is exposed.
set -euo pipefail
cd "$(dirname "$0")/.."

# Never overlap two cycles (a drain run can outlast the cron interval).
exec 9>/tmp/insider-intel-spark-refresh.lock
flock -n 9 || { echo "spark_refresh: previous cycle still running, skipping"; exit 0; }

set -a
# shellcheck source=/dev/null
source .env.spark
set +a
: "${SPARK_CORPUS_BUCKET:?set in .env.spark}"
: "${SPARK_RELOAD_URL:?set in .env.spark}"
: "${ADMIN_API_TOKEN:?set in .env.spark}"

# Bucket root == /app/data layout. Sync only the prefixes the pipeline owns —
# never export/ (workflow-published) — and never with delete flags: additive
# rsync means a partial failure can't destroy bucket state.
PREFIXES=(raw processed state config)

echo "spark_refresh: pull $(date -u +%FT%TZ)"
for p in "${PREFIXES[@]}"; do
  mkdir -p "data/${p}"
  gsutil -m rsync -r "${SPARK_CORPUS_BUCKET}/${p}" "data/${p}" || [ "${p}" = "config" ]
done

echo "spark_refresh: pipeline"
docker compose -f docker-compose.spark.yml run --rm refresh

echo "spark_refresh: push"
for p in "${PREFIXES[@]}"; do
  gsutil -m rsync -r "data/${p}" "${SPARK_CORPUS_BUCKET}/${p}"
done

echo "spark_refresh: reload"
curl -fsS -X POST -H "Authorization: Bearer ${ADMIN_API_TOKEN}" "${SPARK_RELOAD_URL}" \
  && echo "" && echo "spark_refresh: done $(date -u +%FT%TZ)"
