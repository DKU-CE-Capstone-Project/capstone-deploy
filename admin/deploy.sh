#!/usr/bin/env bash
# Explicitly invoked only for deployment. No service-account keys or public IAM grant.
set -euo pipefail
: "${GCP_PROJECT_ID:?Set the target project explicitly}"
: "${ADMIN_SERVICE_ACCOUNT:?Set the read-only admin runtime identity}"
: "${ADMIN_IMAGE:?Set the built Artifact Registry image URL}"
: "${ADMIN_TOKEN_SECRET:?Set the Secret Manager secret name containing ADMIN_STATUS_TOKEN}"
: "${ADMIN_ENV_FILE:?Set the reviewed environment YAML path}"
gcloud run deploy econmind-admin \
  --project "$GCP_PROJECT_ID" --region asia-northeast3 \
  --image "$ADMIN_IMAGE" --service-account "$ADMIN_SERVICE_ACCOUNT" \
  --no-allow-unauthenticated --ingress all --min-instances 0 --max-instances 2 \
  --memory 256Mi --cpu 1 --timeout 180 --concurrency 8 \
  --env-vars-file "$ADMIN_ENV_FILE" \
  --set-secrets "ADMIN_STATUS_TOKEN=${ADMIN_TOKEN_SECRET}:latest"
