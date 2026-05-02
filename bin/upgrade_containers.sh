#!/bin/bash -e

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

# Rename legacy ~/screenly, ~/.screenly, ~/screenly_assets paths to
# their anthias equivalents. The helper self-relocates and re-execs
# from /tmp if it lives inside the dir being renamed, so this also
# handles the case where the running script's path was ~/screenly/...
# Idempotent / no-op on fresh installs and post-migration runs.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"${SCRIPT_DIR}/migrate_legacy_paths.sh"

# Export various environment variables
export MY_IP=$(ip -4 route get 8.8.8.8 | awk {'print $7'} | tr -d '\n')
TOTAL_MEMORY_KB=$(grep MemTotal /proc/meminfo | awk {'print $2'})
export VIEWER_MEMORY_LIMIT_KB=$(echo "$TOTAL_MEMORY_KB" \* 0.8 | bc)
export SHM_SIZE_KB="$(echo "$TOTAL_MEMORY_KB" \* 0.3 | bc | cut -d'.' -f1)"
GIT_BRANCH="${GIT_BRANCH:-master}"

MODE="${MODE:-pull}"
if [[ ! "$MODE" =~ ^(pull|build)$ ]]; then
    echo "Invalid mode: $MODE"
    echo "Usage: MODE=(pull|build) $0"
    exit 1
fi

# The `getmac` module might exit with non-zero exit code if no MAC address is found.
set +e
export MAC_ADDRESS=`${HOME}/installer_venv/bin/python -m getmac`
set -e

if [ -z "$DOCKER_TAG" ]; then
    export DOCKER_TAG="latest"
fi

# Detect Raspberry Pi version. Pi 4 is always treated as pi4-64 (the
# 32-bit pi4 image stream was retired with the Trixie upgrade); legacy
# 0.19.5-and-older 32-bit pi4 deployments stay on whatever DOCKER_TAG
# they were already running and don't reach this code path.
if [ ! -f /proc/device-tree/model ] && [ "$(uname -m)" = "x86_64" ]; then
    export DEVICE_TYPE="x86"
elif grep -qF "Raspberry Pi 5" /proc/device-tree/model || grep -qF "Compute Module 5" /proc/device-tree/model; then
    export DEVICE_TYPE="pi5"
elif grep -qF "Raspberry Pi 4" /proc/device-tree/model || grep -qF "Compute Module 4" /proc/device-tree/model; then
    export DEVICE_TYPE="pi4-64"
elif grep -qF "Raspberry Pi 3" /proc/device-tree/model || grep -qF "Compute Module 3" /proc/device-tree/model; then
    export DEVICE_TYPE="pi3"
elif grep -qF "Raspberry Pi 2" /proc/device-tree/model; then
    export DEVICE_TYPE="pi2"
else
    echo "Unsupported Raspberry Pi model. Anthias supports Pi 2/3/4/5 and x86." >&2
    exit 1
fi

if [[ -n $(docker ps | grep srly-ose) ]]; then
    # @TODO: Rename later
    set +e
    docker container rename srly-ose-server anthias-server
    docker container rename srly-ose-viewer anthias-viewer
    set -e
fi

# Drop legacy containers no longer in the compose file:
#   * nginx / websocket — folded into anthias-server (uvicorn).
#   * wifi-connect      — service removed; nmcli/nmtui is the supported
#                          path now.
#   * anthias-celery / srly-ose-celery containers from the era when
#     celery had its own image. The new compose file recreates the
#     anthias-celery container against ghcr.io/screenly/anthias-server,
#     so the old container (still pointing at the deleted celery image)
#     must be removed first or the server-image-backed replacement
#     can't take its name.
# Volumes are shared across services, so removing the containers is safe.
set +e
docker rm -f \
    anthias-nginx anthias-websocket anthias-wifi-connect \
    srly-ose-nginx srly-ose-websocket srly-ose-wifi-connect \
    anthias-celery srly-ose-celery \
    >/dev/null 2>&1
set -e

cat /home/${USER}/anthias/docker-compose.yml.tmpl \
    | envsubst \
    > /home/${USER}/anthias/docker-compose.yml

if [[ "$DEVICE_TYPE" =~ ^(x86|pi5)$ ]]; then
    sed -i '/devices:/ {N; /\n.*\/dev\/vchiq:\/dev\/vchiq/d}' \
        /home/${USER}/anthias/docker-compose.yml
fi

COMPOSE_FILES=(-f /home/${USER}/anthias/docker-compose.yml)
SSL_OVERRIDE=/home/${USER}/anthias/docker-compose.ssl.override.yml
if [[ -f "$SSL_OVERRIDE" ]]; then
    COMPOSE_FILES+=(-f "$SSL_OVERRIDE")
fi

sudo -E docker compose "${COMPOSE_FILES[@]}" ${MODE}

if [ -f /var/run/reboot-required ]; then
    exit 0
fi

sudo -E docker compose "${COMPOSE_FILES[@]}" up -d
