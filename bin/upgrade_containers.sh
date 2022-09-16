#!/bin/bash -e

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

# Export various environment variables
export MY_IP=$(ip -4 route get 8.8.8.8 | awk {'print $7'} | tr -d '\n')
TOTAL_MEMORY_KB=$(grep MemTotal /proc/meminfo | awk {'print $2'})
export VIEWER_MEMORY_LIMIT_KB=$(echo "$TOTAL_MEMORY_KB" \* 0.7 | bc)

# Hard code this to latest for now.
export DOCKER_TAG="latest"

# Detect Raspberry Pi version
if grep -qF "Raspberry Pi 4" /proc/device-tree/model; then
    export DEVICE_TYPE="pi4"
elif grep -qF "Raspberry Pi 3" /proc/device-tree/model; then
    export DEVICE_TYPE="pi3"
elif grep -qF "Raspberry Pi 2" /proc/device-tree/model; then
    export DEVICE_TYPE="pi2"
else
    # If all else fail, assume pi1
    export DEVICE_TYPE="pi1"
fi

sudo -E docker compose \
    -f /home/pi/screenly/docker-compose.yml \
    -f /home/pi/screenly/docker-compose.override.yml \
    pull

sudo -E docker compose \
    -f /home/pi/screenly/docker-compose.yml \
    -f /home/pi/screenly/docker-compose.override.yml \
    up -d
