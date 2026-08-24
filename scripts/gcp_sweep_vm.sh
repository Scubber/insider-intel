#!/usr/bin/env bash
# One-paste launcher for the IndiaCourts full-history bulk sweep on a GCP
# spot VM — the zero-home-bandwidth venue (S3→GCP ingress is free and fast;
# output flows back through the corpus bucket the nightly cycle already
# pulls). Operator-run; expects gcloud authed to the project.
#
#   bash scripts/gcp_sweep_vm.sh            # create + start the sweep VM
#   bash scripts/gcp_sweep_vm.sh status     # tail the sweep's serial output
#   bash scripts/gcp_sweep_vm.sh delete     # tear the VM down
#
# Costs (spot, us-east1): c2d-standard-32 ≈ $0.20–0.30/hr → ~$25–50 for the
# full ~18.5M-judgment sweep at the measured extraction cost. Preemption is
# fine: partition markers make the sweep resumable and the VM restarts the
# container on boot. Sizing law: pypdf extraction is GIL-bound, so the
# throughput knob is INDIACOURTS_SWEEP_EXTRACT_PROCS (process pool), not
# threads — the first run wasted 8h at ~1 effective core before this.
set -euo pipefail

PROJECT="${PROJECT:-insider-intel-502413}"
ZONE="${ZONE:-us-east1-b}"
NAME="${NAME:-indiacourts-sweep}"
MACHINE="${MACHINE:-c2d-standard-32}"
BUCKET="gs://${PROJECT}-corpus"
SPOOL_PREFIX="${BUCKET}/raw/sweeps/indiacourts"
STATE_PREFIX="${BUCKET}/raw/sweeps/indiacourts-state"
SRC_TARBALL="${BUCKET}/raw/sweeps/indiacourts-src.tar.gz"

case "${1:-create}" in
  status)
    gcloud compute instances get-serial-port-output "$NAME" \
      --zone "$ZONE" --project "$PROJECT" | tail -40
    echo "--- status.json (per-partition progress) ---"
    gcloud storage cat "${SPOOL_PREFIX}/status.json" 2>/dev/null \
      || echo "(no status.json in the bucket spool yet)"
    echo "--- heartbeat.log (container pulse, refreshed every 5min) ---"
    gcloud storage cat "${SPOOL_PREFIX}/heartbeat.log" 2>/dev/null \
      || echo "(no heartbeat yet)"
    echo "--- spooled chunks ---"
    gcloud storage ls -l "${SPOOL_PREFIX}/*.jsonl" 2>/dev/null | tail -15 \
      || echo "(no match chunks yet)"
    exit 0
    ;;
  delete)
    gcloud compute instances delete "$NAME" --zone "$ZONE" --project "$PROJECT" --quiet
    exit 0
    ;;
esac

# One-time project prep, idempotent and best-effort: on an owner account this
# self-serves; the spark-corpus SA (sparky's identity) lacks IAM rights, so
# there these steps no-op and the owner's one-time grant block (HANDOFF
# thread #8) must already have been run.
gcloud services enable compute.googleapis.com --project "$PROJECT" 2>/dev/null || true
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)' 2>/dev/null || true)
if [ -n "$PROJECT_NUMBER" ]; then
  gcloud storage buckets add-iam-policy-binding "$BUCKET" \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role=roles/storage.objectAdmin >/dev/null 2>&1 \
    || echo "NOTE: could not (re)grant objectAdmin — fine if the owner grant block already ran"
fi

# The repo is private, so the VM gets its source as a tarball through the
# bucket (this script runs from a checkout — usually sparky's).
git -C "$(dirname "$0")/.." archive --format=tar.gz -o /tmp/indiacourts-src.tar.gz HEAD
gcloud storage cp /tmp/indiacourts-src.tar.gz "$SRC_TARBALL"

STARTUP=$(cat <<EOS
#!/bin/bash
set -euo pipefail
apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates
# Docker CE via the convenience script: the repo Dockerfile uses heredocs,
# which bare docker.io lacks BuildKit for (known gotcha).
curl -fsSL https://get.docker.com | sh
mkdir -p /opt/insider-intel
gcloud storage cp ${SRC_TARBALL} /opt/src.tar.gz
tar xzf /opt/src.tar.gz -C /opt/insider-intel
cd /opt/insider-intel
docker build --target spark -t insider-intel-spark .
mkdir -p /opt/data/raw/sweep_spool /opt/data/state/indiacourts
# Resume state from any prior attempt (spot preemption survival).
gcloud storage rsync -r ${STATE_PREFIX} /opt/data/state/indiacourts || true
# The image runs as uid 1000 ("app") — root-owned dirs are unwritable to it.
# The first live run lost EVERY durable write (chunks, status.json, partition
# markers) to EACCES and crash-looped; chown must follow the resume rsync,
# which runs as root and re-creates root-owned files.
chown -R 1000:1000 /opt/data
# Sweep container: the sweep needs no secrets — the dataset is public and
# the spool syncs via the VM's service account, never from inside.
docker rm -f sweep 2>/dev/null || true
# OCR stays OFF for the bulk pass: the 2026-08-24 07:30Z watch measured a
# 95% throughput collapse (300k+/h -> ~14.5k/h) once workers hit scan-heavy
# benches — each inline Tesseract pass costs ~2min of CPU, so a ~6% scanned
# share makes the whole sweep OCR-bound (~50-day projection). Scans are
# counted (scanned_skipped) and skipped; a targeted OCR pass revisits them
# later. The nightly sparky lane keeps its own OCR path unchanged.
docker run -d --name sweep --restart on-failure \
  -e INDIACOURTS_ENABLED=true \
  -e INDIACOURTS_SWEEP_WORKERS=32 \
  -e INDIACOURTS_SWEEP_EXTRACT_PROCS=28 \
  -e INDIACOURTS_SWEEP_OCR=false \
  -e INDIACOURTS_OCR_COMMAND="python -m apps.aggregator.ocr_pdf" \
  -v /opt/data:/app/data \
  insider-intel-spark python -m apps.aggregator sweep_indiacourts_bulk -v
# Sync loop: chunks + status up, state up (resume), until the sweep exits.
# heartbeat.log ships the container's latest lines to the bucket each cycle
# so remote monitoring sees a live pulse instead of hour-long blind windows.
while docker ps -q -f name=sweep | grep -q .; do
  { date -u +%FT%TZ; docker logs --tail 14 sweep 2>&1; } \
    > /opt/data/raw/sweep_spool/heartbeat.log || true
  gcloud storage rsync /opt/data/raw/sweep_spool ${SPOOL_PREFIX} || true
  gcloud storage rsync -r /opt/data/state/indiacourts ${STATE_PREFIX} || true
  sleep 300
done
{ date -u +%FT%TZ; echo "SWEEP CONTAINER EXITED:"; docker logs --tail 15 sweep 2>&1; } \
  > /opt/data/raw/sweep_spool/heartbeat.log || true
gcloud storage rsync /opt/data/raw/sweep_spool ${SPOOL_PREFIX} || true
gcloud storage rsync -r /opt/data/state/indiacourts ${STATE_PREFIX} || true
echo "SWEEP-COMPLETE \$(date -u)" > /opt/done
EOS
)

# --metadata parses its value as a comma-separated dict, which mangles any
# real script — the file form is the only safe way to pass one.
STARTUP_FILE=$(mktemp /tmp/indiacourts-sweep-startup.XXXXXX.sh)
printf '%s\n' "$STARTUP" > "$STARTUP_FILE"
gcloud compute instances create "$NAME" \
  --project "$PROJECT" --zone "$ZONE" \
  --machine-type "$MACHINE" \
  --provisioning-model=SPOT --instance-termination-action=STOP \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=50GB \
  --scopes=storage-rw \
  --metadata-from-file=startup-script="$STARTUP_FILE"
rm -f "$STARTUP_FILE"

echo
echo "VM '$NAME' creating (spot ${MACHINE}, ${ZONE}). Boot takes a few minutes:"
echo "docker installs, the source tarball builds, then the sweep streams tars"
echo "and rsyncs chunks/state to ${SPOOL_PREFIX} every 5 minutes."
echo "Watch: bash scripts/gcp_sweep_vm.sh status   Tear down: bash scripts/gcp_sweep_vm.sh delete"
