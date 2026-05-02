#!/usr/bin/env bash
# Mirror immutable <short-hash>-<board> tags onto the floating
# latest-<board> tag for one registry namespace. Called twice from
# .github/workflows/docker-build.yaml — once for GHCR (hard-fail)
# and once for Docker Hub (soft-fail via continue-on-error).
#
# Reads NAMESPACE from env (e.g. ghcr.io/screenly/anthias or
# screenly/anthias). Image refs are built as
# "${NAMESPACE}-${service}:${tag}" — note the trailing dash in the
# namespace's effective form, matching the existing
# anthias-{server,redis,viewer} image naming.
#
# A preflight verifies every <short-hash>-<board> source tag is
# resolvable before any retag fires, so a missing source tag fails
# before mutating the registry. Each registry op runs in 5 attempts
# of exponential backoff (2s, 4s, 8s, 16s) so a transient 429 / 5xx
# doesn't strand latest-* half-mirrored. Both `imagetools inspect`
# and `imagetools create` are idempotent: inspect is read-only, and
# create overwrites the tag with the same manifest digest on retry.

set -euo pipefail

: "${NAMESPACE:?NAMESPACE env var must be set}"

GIT_SHORT_HASH=$(git rev-parse --short=7 HEAD)
BOARDS=(pi2 pi3 pi4-64 pi5 x86)
SERVICES=(server redis viewer)

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
  echo "Giving up after 5 attempts: $*" >&2
  return 1
}

echo "::group::[${NAMESPACE}] Preflight: verify every <short-hash>-<board> source tag exists"
for service in "${SERVICES[@]}"; do
  for board in "${BOARDS[@]}"; do
    src="${NAMESPACE}-${service}:${GIT_SHORT_HASH}-${board}"
    echo "Verifying ${src}"
    retry docker buildx imagetools inspect --raw "${src}" >/dev/null
  done
done
echo "::endgroup::"

echo "::group::[${NAMESPACE}] Mirror <short-hash>-<board> -> latest-<board>"
for service in "${SERVICES[@]}"; do
  for board in "${BOARDS[@]}"; do
    src="${NAMESPACE}-${service}:${GIT_SHORT_HASH}-${board}"
    dst="${NAMESPACE}-${service}:latest-${board}"
    echo "Mirroring ${src} -> ${dst}"
    retry docker buildx imagetools create -t "${dst}" "${src}"
  done
done
echo "::endgroup::"
