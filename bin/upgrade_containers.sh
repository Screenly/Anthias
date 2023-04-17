#!/bin/bash -e

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

# Export various environment variables
export MY_IP=$(ip -4 route get 8.8.8.8 | awk {'print $7'} | tr -d '\n')
TOTAL_MEMORY_KB=$(grep MemTotal /proc/meminfo | awk {'print $2'})
export VIEWER_MEMORY_LIMIT_KB=$(echo "$TOTAL_MEMORY_KB" \* 0.8 | bc)

# Hard code this to latest for now.
export DOCKER_TAG="latest"

# nico start - debug
filename=$(basename "$0")
echo "[$filename] #01 - groups $USER: $(groups $USER)"
# nico end

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

# nico start - debug
echo "[$filename] #02 - groups $USER: $(groups $USER)"
# nico end

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

# nico start - debug
echo "[$filename] #03 - groups $USER: $(groups $USER)"
# nico end

cat /home/${USER}/screenly/docker-compose.yml.tmpl \
    | envsubst \
    > /home/${USER}/screenly/docker-compose.yml

# nico start - debug
echo "[$filename] #04 - groups $USER: $(groups $USER)"
# nico end

sudo -E docker compose \
    -f /home/${USER}/screenly/docker-compose.yml \
    pull

# nico start - debug
echo "[$filename] #05 - groups $USER: $(groups $USER)"
# nico end

sudo -E docker compose \
    -f /home/${USER}/screenly/docker-compose.yml \
    up -d

# nico start - debug
echo "[$filename] #06 - groups $USER: $(groups $USER)"
# nico end
