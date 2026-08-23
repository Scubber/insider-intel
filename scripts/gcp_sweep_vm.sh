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
# Costs (spot, us-east1): c2d-standard-16 ≈ $0.10–0.15/hr → ~$20–40 for the
# full ~18.5M-judgment sweep. Preemption is fine: partition markers make the
# sweep resumable and the VM restarts the container on boot.
set -euo pipefail

PROJECT="${PROJECT:-insider-intel-502413}"
ZONE="${ZONE:-us-east1-b}"
NAME="${NAME:-indiacourts-sweep}"
MACHINE="${MACHINE:-c2d-standard-16}"
BUCKET="gs://${PROJECT}-corpus"
SPOOL_PREFIX="${BUCKET}/raw/sweeps/indiacourts"
STATE_PREFIX="${BUCKET}/raw/sweeps/indiacourts-state"
REPO="${REPO:-https://github.com/Scubber/insider-intel.git}"

case "${1:-create}" in
  status)
    gcloud compute instances get-serial-port-output "$NAME" \
      --zone "$ZONE" --project "$PROJECT" | tail -60
    echo "---"
    gcloud storage cat "${SPOOL_PREFIX}/status.json" 2>/dev/null \
      || echo "(no status.json in the bucket spool yet)"
    exit 0
    ;;
  delete)
    gcloud compute instances delete "$NAME" --zone "$ZONE" --project "$PROJECT" --quiet
    exit 0
    ;;
esac

STARTUP=$(cat <<EOS
#!/bin/bash
set -euo pipefail
apt-get update && apt-get install -y --no-install-recommends docker.io git
git clone --depth 1 ${REPO} /opt/insider-intel
cd /opt/insider-intel
docker build --target spark -t insider-intel-spark .
mkdir -p /opt/data/raw/sweep_spool /opt/data/state/indiacourts
# Resume state from any prior attempt (spot preemption survival).
gcloud storage rsync -r ${STATE_PREFIX} /opt/data/state/indiacourts || true
# Sweep container: the sweep needs no secrets — the dataset is public and
# the spool syncs via the VM's service account, never from inside.
docker rm -f sweep 2>/dev/null || true
docker run -d --name sweep --restart on-failure \
  -e INDIACOURTS_ENABLED=true \
  -e INDIACOURTS_SWEEP_WORKERS=8 \
  -e INDIACOURTS_OCR_COMMAND="python -m apps.aggregator.ocr_pdf" \
  -v /opt/data:/app/data \
  insider-intel-spark python -m apps.aggregator sweep_indiacourts_bulk -v
# Sync loop: chunks + status up, state up (resume), until the sweep exits.
while docker ps -q -f name=sweep | grep -q .; do
  gcloud storage rsync /opt/data/raw/sweep_spool ${SPOOL_PREFIX} || true
  gcloud storage rsync -r /opt/data/state/indiacourts ${STATE_PREFIX} || true
  sleep 300
done
gcloud storage rsync /opt/data/raw/sweep_spool ${SPOOL_PREFIX} || true
gcloud storage rsync -r /opt/data/state/indiacourts ${STATE_PREFIX} || true
echo "SWEEP-COMPLETE \$(date -u)" > /opt/done
EOS
)

gcloud compute instances create "$NAME" \
  --project "$PROJECT" --zone "$ZONE" \
  --machine-type "$MACHINE" \
  --provisioning-model=SPOT --instance-termination-action=STOP \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=50GB \
  --scopes=storage-rw \
  --metadata=startup-script="$STARTUP"

echo
echo "VM '$NAME' creating. The default compute SA needs objectAdmin on ${BUCKET}"
echo "(one-time): gcloud storage buckets add-iam-policy-binding ${BUCKET} \\"
echo "  --member=serviceAccount:\$(gcloud projects describe ${PROJECT} --format='value(projectNumber)')-compute@developer.gserviceaccount.com \\"
echo "  --role=roles/storage.objectAdmin"
echo "Watch: bash scripts/gcp_sweep_vm.sh status   Tear down: bash scripts/gcp_sweep_vm.sh delete"
