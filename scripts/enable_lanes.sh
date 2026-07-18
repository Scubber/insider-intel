#!/usr/bin/env bash
# insider-intel: arm the corpus-refresh job with PACER, X, and LLM credentials.
# Run in WSL where gcloud is logged in:  bash enable_lanes.sh
# Idempotent: re-running is safe. Uses --update-secrets (merges) — never
# --set-secrets, which would WIPE previously attached secrets.
set -euo pipefail

PROJECT=insider-intel-502413
REGION=us-east1
JOB=corpus-refresh
JOB_SA="ingest-job@${PROJECT}.iam.gserviceaccount.com"

secret_exists() { gcloud secrets describe "$1" --project "$PROJECT" >/dev/null 2>&1; }

grant() {
  gcloud secrets add-iam-policy-binding "$1" --project "$PROJECT" \
    --member "serviceAccount:${JOB_SA}" --role roles/secretmanager.secretAccessor \
    --quiet >/dev/null
  echo "  granted accessor on $1"
}

# ---- 1. Anthropic key for the ingest summarizer (case records + ai_summary)
if ! secret_exists ANTHROPIC_API_KEY; then
  echo "Paste your Anthropic API key (input hidden), or press Enter to skip:"
  read -rs ANTHROPIC_KEY
  if [ -n "${ANTHROPIC_KEY}" ]; then
    printf '%s' "$ANTHROPIC_KEY" | gcloud secrets create ANTHROPIC_API_KEY \
      --data-file=- --project "$PROJECT"
    echo "  created secret ANTHROPIC_API_KEY"
  else
    echo "  skipped (summarizer stays off)"
  fi
fi

# ---- 2. Collect every relevant secret that exists into one merged mapping
MAPPINGS=()
declare -A WANT=(
  [PACER_USERNAME]="PACER_USERNAME"
  [PACER_PASSWORD]="PACER_PASSWORD"  # pragma: allowlist secret
  [COURTLISTENER_API_TOKEN]="COURTLISTENER_API_TOKEN"
  [ANTHROPIC_API_KEY]="ANTHROPIC_API_KEY"  # pragma: allowlist secret
  [X_BEARER_TOKEN]="X_BEARER_TOKEN"
  [X_CONSUMER_KEY]="x_consumer_key"     # env var → lowercase secret name
  [X_CONSUMER_SECRET]="x_consumer_secret"  # pragma: allowlist secret
  [REDDIT_CLIENT_ID]="REDDIT_CLIENT_ID"     # free "script" app at reddit.com/prefs/apps
  [REDDIT_CLIENT_SECRET]="REDDIT_CLIENT_SECRET"  # pragma: allowlist secret
)
for ENVVAR in "${!WANT[@]}"; do
  SECRET="${WANT[$ENVVAR]}"
  # accept either exact name or the uppercase variant
  if secret_exists "$SECRET"; then
    :
  elif secret_exists "$ENVVAR"; then
    SECRET="$ENVVAR"
  else
    echo "  (no secret for $ENVVAR — lane stays off)"
    continue
  fi
  grant "$SECRET"
  MAPPINGS+=("${ENVVAR}=${SECRET}:latest")
done

if [ "${#MAPPINGS[@]}" -eq 0 ]; then
  echo "No secrets found — nothing to attach."; exit 0
fi

JOINED=$(IFS=,; echo "${MAPPINGS[*]}")
echo "Attaching to ${JOB}: ${JOINED}"
gcloud run jobs update "$JOB" --region "$REGION" --project "$PROJECT" \
  --update-secrets "$JOINED"

# ---- 3. Enable the summarizer env flag if its key is attached
if printf '%s' "$JOINED" | grep -q ANTHROPIC_API_KEY; then
  gcloud run jobs update "$JOB" --region "$REGION" --project "$PROJECT" \
    --update-env-vars SUMMARIZER_LLM_PROVIDER=anthropic
  echo "  summarizer enabled (anthropic, 15 articles/run cap)"
fi

# ---- 4. Kick one refresh and show the interesting log lines
echo "Running one refresh now (takes a few minutes)…"
gcloud run jobs execute "$JOB" --region "$REGION" --project "$PROJECT" --wait
echo "---- refresh highlights ----"
gcloud logging read "resource.labels.job_name=${JOB}" --project "$PROJECT" \
  --freshness=30m --format='value(textPayload)' \
  | grep -E 'PACER|backfill|Backfilled|Summar|X ingest|Skipping|reloaded|\[OK\]|\[FAIL\]' \
  | head -40 || true
echo "Done. Re-run this script anytime; it only adds what's missing."
