#!/bin/bash

# Deploy a pre-built Anthias release to one board's balenaCloud fleet via
# `balena deploy` — i.e. point the fleet at the GHCR images already
# tagged <short-hash>-<board>, no remote build. Shared by the release
# pipeline (.github/workflows/build-balena-disk-image.yaml) and the
# manual deploy hook (.github/workflows/deploy-balena-manual.yaml) so the
# fleet mapping, compose rendering, and retry logic live in exactly one
# place instead of being copy-pasted per workflow.
#
# Assumes `balena login` has already run (the caller owns the token).
# Re-deploying a version that's already on the fleet is expected and
# safe: balena auto-appends +rev1, +rev2, ... to the release version.
#
# Usage: balena_ota_deploy.sh <board> <version> <git-short-hash>
#   board   one of pi2 | pi3 | pi4-64 | pi5 | x86
#   version raw CalVer, e.g. 2026.05.1 (render_balena_yml.sh normalizes it)

set -euo pipefail

BOARD="${1:?usage: balena_ota_deploy.sh <board> <version> <short-hash>}"
RELEASE_VERSION="${2:?usage: balena_ota_deploy.sh <board> <version> <short-hash>}"
GIT_SHORT_HASH="${3:?usage: balena_ota_deploy.sh <board> <version> <short-hash>}"
export GIT_SHORT_HASH
export SHM_SIZE="${SHM_SIZE:-256mb}"

case "$BOARD" in
    pi2)    FLEET=anthias-pi2 ;;
    pi3)    FLEET=anthias-pi3 ;;
    pi4-64) FLEET=anthias-pi4 ;;
    pi5)    FLEET=anthias-pi5 ;;
    x86)    FLEET=anthias-x86 ;;
    *)
        echo "balena_ota_deploy.sh: unknown board '$BOARD'" >&2
        exit 1
        ;;
esac
export BOARD

# Stamp balena.yml with the release version, then render the compose
# pinned to the <short-hash>-<board> GHCR images.
bin/render_balena_yml.sh balena-deploy "$RELEASE_VERSION"
envsubst < docker-compose.balena.yml.tmpl > balena-deploy/docker-compose.yml

# Pi 5 and x86 don't expose /dev/vchiq; strip the bind mount.
if [[ "$BOARD" =~ ^(pi5|x86)$ ]]; then
    sed -i '/devices:/ {N; /\n.*\/dev\/vchiq:\/dev\/vchiq/d}' \
        balena-deploy/docker-compose.yml
fi

# Wrapped in a 3-attempt retry because balena cloud routinely
# 5xx/ESOCKETTIMEDOUTs the upload step.
for attempt in 1 2 3; do
    if balena deploy "screenly_ose/$FLEET" --source balena-deploy; then
        exit 0
    fi
    echo "balena deploy attempt $attempt failed; backing off"
    sleep $((30 + RANDOM % 120))
done
echo "::error::balena deploy failed for $FLEET after 3 attempts"
exit 1
