#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euo pipefail

GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
GIT_SHORT_HASH=$(git rev-parse --short HEAD)
GIT_HASH=$(git rev-parse HEAD)

if [ "$GIT_BRANCH" = "master" ]; then
    DOCKER_TAG="latest"
else
    DOCKER_TAG="$GIT_BRANCH"
fi

DOCKER_BUILD_ARGS=("buildx" "build" "--load")
echo 'Make sure you ran `docker buildx create --use` before the command'

if [ -n "${CLEAN_BUILD+x}" ]; then
    DOCKER_BUILD_ARGS+=("--no-cache")
fi

# Set various variables for the Dockerfiles to use
export BASE_IMAGE_TAG=buster
export DEBIAN_VERSION=buster
export QT_VERSION=5.15.2
export GIT_SHORT_HASH="$(git rev-parse --short HEAD)"
export GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
export GIT_HASH="$(git rev-parse HEAD)"
export WEBVIEW_GIT_HASH=0b6d49359133246659b9ba1d8dd883e3fc5c9a91
export WEBVIEW_BASE_URL="https://github.com/Screenly/Anthias/releases/download/WebView-v0.2.1"

# Detect what platform
if [ ! -f /proc/device-tree/model ]; then
    export BOARD="x86"
    export BASE_IMAGE=debian
elif grep -qF "Raspberry Pi 4" /proc/device-tree/model; then
    export BASE_IMAGE=balenalib/raspberrypi3-debian
    export BOARD="pi4"
elif grep -qF "Raspberry Pi 3" /proc/device-tree/model; then
    export BOARD="pi3"
    export BASE_IMAGE=balenalib/raspberrypi3-debian
elif grep -qF "Raspberry Pi 2" /proc/device-tree/model; then
    export BOARD="pi2"
    export BASE_IMAGE=balenalib/raspberry-pi2
elif grep -qF "Raspberry Pi 1" /proc/device-tree/model; then
    export BOARD="pi1"
    export BASE_IMAGE=balenalib/raspberry-pi
fi

for container in base server celery redis websocket nginx viewer wifi-connect 'test'; do
    echo "Building $container"
    cat "docker/Dockerfile.$container.tmpl" | envsubst > "docker/Dockerfile.$container" 

    # If we're running on x86, remove all Pi specific packages
    if [ "$BOARD" == 'x86' ]; then
        sed -i '/libraspberrypi0/d' docker/Dockerfile.*
        sed -i '/omxplayer/d' docker/Dockerfile.*
    fi

    docker "${DOCKER_BUILD_ARGS[@]}" \
        --build-arg "GIT_HASH=$GIT_HASH" \
        --build-arg "GIT_BRANCH=$GIT_BRANCH" \
        --build-arg "GIT_SHORT_HASH=$GIT_SHORT_HASH" \
        --build-arg "PI_VERSION=$BOARD" \
        --cache-from "type=local,src=/tmp/.buildx-cache" \
        --cache-from "type=registry,ref=screenly/srly-ose-$container:$DOCKER_TAG" \
        --cache-to "type=local,dest=/tmp/.buildx-cache" \
        -f "docker/Dockerfile.$container" \
        -t "screenly/srly-ose-$container:$DOCKER_TAG" .

    # Push if the push flag is set and not cross compiling
    if [[ ( -n "${PUSH+x}" && -z "${CROSS_COMPILE+x}" ) ]]; then
        docker push "screenly/srly-ose-$container:$DOCKER_TAG-$pi_version"
    fi
done