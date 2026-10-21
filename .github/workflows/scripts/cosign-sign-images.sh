#!/usr/bin/env bash
# Keyless-sign the server, viewer and redis images this board's buildx
# job just pushed. Called twice from .github/workflows/docker-build.yaml
# — once for GHCR (hard-fail) and once for Docker Hub (soft-fail via
# continue-on-error).
#
# Reads NAMESPACE (e.g. ghcr.io/screenly/anthias or
# docker.io/screenly/anthias) and SERVER_DIGEST / VIEWER_DIGEST /
# REDIS_DIGEST from env. Signs "${NAMESPACE}-${service}@${digest}",
# never a tag, so the signature covers exactly the manifest that was
# pushed and every tag later pointed at it.
#
# cosign takes its identity from the job's GitHub OIDC token (the job
# needs `id-token: write`), so the certificate names this workflow and
# ref; bin/verify_image_signatures.sh on the device pins both. Signing
# the same digest twice only adds a second signature, so the 5-attempt
# exponential backoff is safe to retry on a transient 429 / 5xx.

set -euo pipefail

: "${NAMESPACE:?NAMESPACE env var must be set}"
: "${SERVER_DIGEST:?SERVER_DIGEST env var must be set}"
: "${VIEWER_DIGEST:?VIEWER_DIGEST env var must be set}"
: "${REDIS_DIGEST:?REDIS_DIGEST env var must be set}"

retry() {
  local attempt
  for attempt in 1 2 3 4 5; do
    if "$@"; then
      return 0
    fi
    if [ "${attempt}" -lt 5 ]; then
      local delay=$((2 ** attempt))
      echo "Attempt ${attempt} failed; retrying in ${delay}s..." >&2
      sleep "${delay}"
    fi
  done
  return 1
}

declare -A DIGESTS=(
  [server]="${SERVER_DIGEST}"
  [viewer]="${VIEWER_DIGEST}"
  [redis]="${REDIS_DIGEST}"
)

for service in server viewer redis; do
  ref="${NAMESPACE}-${service}@${DIGESTS[${service}]}"
  echo "Signing ${ref}"
  retry cosign sign --yes "${ref}"
done
