#!/usr/bin/env bash
# Build + deploy insider-intel FastAPI to Google Cloud Run.
#
# Prerequisites:
#   - gcloud CLI authenticated (gcloud auth login)
#   - GCP project with billing, Cloud Run + Artifact Registry APIs enabled
#   - Local corpus at data/processed/articles.jsonl (after ingest/process)
#
# Usage (from insider-intel/):
#   export GCP_PROJECT=your-project-id
#   export GCP_REGION=us-east1          # optional
#   export ARTIFACT_REPO=insider-intel  # optional
#   ./scripts/deploy_cloud_run.sh
#
# Then map custom domain api.intel.thederpweb.com in Cloud Run + Route 53
# (see docs/hosting.md).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROJECT="${GCP_PROJECT:-${GOOGLE_CLOUD_PROJECT:-}}"
REGION="${GCP_REGION:-us-east1}"
REPO="${ARTIFACT_REPO:-insider-intel}"
SERVICE="${CLOUD_RUN_SERVICE:-insider-intel-api}"
IMAGE_NAME="${IMAGE_NAME:-insider-intel-api}"

if [[ -z "$PROJECT" ]]; then
  echo "Set GCP_PROJECT (or GOOGLE_CLOUD_PROJECT) to your GCP project id." >&2
  exit 1
fi

CORPUS="data/processed/articles.jsonl"
if [[ ! -f "$CORPUS" ]]; then
  echo "Missing $CORPUS — run aggregator process locally first." >&2
  exit 1
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud CLI not found. Install: https://cloud.google.com/sdk/docs/install" >&2
  exit 1
fi

echo "Project=$PROJECT Region=$REGION Service=$SERVICE"
gcloud config set project "$PROJECT"

echo "Ensuring APIs…"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com --quiet

if ! gcloud artifacts repositories describe "$REPO" --location="$REGION" >/dev/null 2>&1; then
  echo "Creating Artifact Registry repo $REPO…"
  gcloud artifacts repositories create "$REPO" \
    --repository-format=docker \
    --location="$REGION" \
    --description="insider-intel API images"
fi

IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${IMAGE_NAME}:$(date -u +%Y%m%d%H%M%S)"
IMAGE_LATEST="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${IMAGE_NAME}:latest"

echo "Configuring docker auth for Artifact Registry…"
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

echo "Building $IMAGE …"
docker build -t "$IMAGE" -t "$IMAGE_LATEST" .

echo "Pushing…"
docker push "$IMAGE"
docker push "$IMAGE_LATEST"

CORS_ORIGINS="${CORS_ORIGINS:-https://intel.thederpweb.com,https://td3.dev,https://scubber.github.io}"

echo "Deploying Cloud Run service $SERVICE…"
# Optional secrets: create first, then pass e.g.
#   --set-secrets=XAI_API_KEY=XAI_API_KEY:latest,COURTLISTENER_API_TOKEN=COURTLISTENER_API_TOKEN:latest
DEPLOY_ARGS=(
  run deploy "$SERVICE"
  --image="$IMAGE"
  --region="$REGION"
  --platform=managed
  --allow-unauthenticated
  --port=8080
  --memory=1Gi
  --cpu=1
  --min-instances=0
  --max-instances=3
  --set-env-vars="PROCESSED_ARTICLES_PATH=/app/data/processed/articles.jsonl,CORS_ORIGINS=${CORS_ORIGINS}"
)

if [[ -n "${CLOUD_RUN_SECRETS:-}" ]]; then
  DEPLOY_ARGS+=(--set-secrets="${CLOUD_RUN_SECRETS}")
fi

gcloud "${DEPLOY_ARGS[@]}"

URL="$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')"
echo ""
echo "Deployed: $URL"
echo "Health:   curl -sS \"$URL/health\""
echo ""
echo "Next:"
echo "  1. Map custom domain api.intel.thederpweb.com → this service (Cloud Run domain mapping)"
echo "  2. Route 53 CNAME/ALIAS for api.intel.thederpweb.com"
echo "  3. Confirm https://intel.thederpweb.com uses live API (web/config.js)"
echo "  4. Optional: CLOUD_RUN_SECRETS='XAI_API_KEY=XAI_API_KEY:latest' ./scripts/deploy_cloud_run.sh"
