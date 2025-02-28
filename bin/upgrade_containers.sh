#!/bin/bash -e

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

# Export various environment variables
export MY_IP=$(ip -4 route get 8.8.8.8 | awk {'print $7'} | tr -d '\n')
TOTAL_MEMORY_KB=$(grep MemTotal /proc/meminfo | awk {'print $2'})
export VIEWER_MEMORY_LIMIT_KB=$(echo "$TOTAL_MEMORY_KB" \* 0.8 | bc)
export SHM_SIZE_KB="$(echo "$TOTAL_MEMORY_KB" \* 0.3 | bc | cut -d'.' -f1)"
export GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD)

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

# Detect Raspberry Pi version
if [ ! -f /proc/device-tree/model ] && [ "$(uname -m)" = "x86_64" ]; then
    export DEVICE_TYPE="x86"
elif grep -qF "Raspberry Pi 5" /proc/device-tree/model || grep -qF "Compute Module 5" /proc/device-tree/model; then
    export DEVICE_TYPE="pi5"
elif grep -qF "Raspberry Pi 4" /proc/device-tree/model || grep -qF "Compute Module 4" /proc/device-tree/model; then
    if [ "$(getconf LONG_BIT)" = "64" ]; then
        export DEVICE_TYPE="pi4-64"
    else
        export DEVICE_TYPE="pi4"
    fi
elif grep -qF "Raspberry Pi 3" /proc/device-tree/model || grep -qF "Compute Module 3" /proc/device-tree/model; then
    export DEVICE_TYPE="pi3"
elif grep -qF "Raspberry Pi 2" /proc/device-tree/model; then
    export DEVICE_TYPE="pi2"
else
    # If all else fail, assume pi1
    export DEVICE_TYPE="pi1"
fi

if [[ -n $(docker ps | grep srly-ose) ]]; then
    # @TODO: Rename later
    set +e
    docker container rename srly-ose-wifi-connect anthias-wifi-connect
    docker container rename srly-ose-server anthias-server
    docker container rename srly-ose-viewer anthias-viewer
    docker container rename srly-ose-celery anthias-celery
    docker container rename srly-ose-websocket anthias-websocket
    docker container rename srly-ose-nginx anthias-nginx
    set -e
fi

cat /home/${USER}/screenly/docker-compose.yml.tmpl \
    | envsubst \
    > /home/${USER}/screenly/docker-compose.yml

if [[ "$DEVICE_TYPE" =~ ^(x86|pi5)$ ]]; then
    sed -i '/devices:/ {N; /\n.*\/dev\/vchiq:\/dev\/vchiq/d}' \
        /home/${USER}/screenly/docker-compose.yml
fi

sudo -E docker compose \
    -f /home/${USER}/screenly/docker-compose.yml \
    ${MODE}

if [ -f /var/run/reboot-required ]; then
    exit 0
fi

sudo -E docker compose \
    -f /home/${USER}/screenly/docker-compose.yml \
    up -d
