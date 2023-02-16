#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euo pipefail

GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
GIT_SHORT_HASH=$(git rev-parse --short HEAD)
GIT_HASH=$(git rev-parse HEAD)
BASE_IMAGE_TAG=buster
DEBIAN_VERSION=buster
QT_VERSION=5.15.2
WEBVIEW_GIT_HASH=0b6d49359133246659b9ba1d8dd883e3fc5c9a91
WEBVIEW_BASE_URL="https://github.com/Screenly/Anthias/releases/download/WebView-v0.2.1"

DOCKER_BUILD_ARGS=("buildx" "build" "--load")
echo 'Make sure you ran `docker buildx create --use` before the command'

if [ -n "${CLEAN_BUILD+x}" ]; then
    DOCKER_BUILD_ARGS+=("--no-cache")
fi

# Set various variables for the Dockerfiles to use

# Detect what platform
if [ ! -f /proc/device-tree/model ] && [ -z "${BUILD_TARGET+x}" ]; then
    BOARD="x86"
    BASE_IMAGE=debian
    TARGET_PLATFORM=linux/amd64
elif grep -qF "Raspberry Pi 4" /proc/device-tree/model || [ "${BUILD_TARGET+x}" == 'pi4' ]; then
    BASE_IMAGE=balenalib/raspberrypi3-debian
    BOARD="pi4"
    TARGET_PLATFORM=linux/arm/v8
elif grep -qF "Raspberry Pi 3" /proc/device-tree/model || [ "${BUILD_TARGET+x}" == 'pi3' ]; then
    BOARD="pi3"
    BASE_IMAGE=balenalib/raspberrypi3-debian
    TARGET_PLATFORM=linux/arm/v7
elif grep -qF "Raspberry Pi 2" /proc/device-tree/model || [ "${BUILD_TARGET+x}" == 'pi2' ]; then
    BOARD="pi2"
    BASE_IMAGE=balenalib/raspberry-pi2
    TARGET_PLATFORM=linux/arm/v6
elif grep -qF "Raspberry Pi 1" /proc/device-tree/model || [ "${BUILD_TARGET+x}" == 'pi4' ]; then
    BOARD="pi1"
    BASE_IMAGE=balenalib/raspberry-pi
    TARGET_PLATFORM=linux/arm/v6
fi

if [ "$GIT_BRANCH" = "master" ]; then
    DOCKER_TAG="latest-$BOARD"
else
    DOCKER_TAG="$GIT_BRANCH-$BOARD"
fi


for container in base server celery redis websocket nginx viewer wifi-connect 'test'; do
    echo "Building $container"
    cat "docker/Dockerfile.$container.tmpl" | envsubst > "docker/Dockerfile.$container" 

    # If we're running on x86, remove all Pi specific packages
    if [ "$BOARD" == 'x86' ]; then
        sed -i '/libraspberrypi0/d' $(find docker/ -maxdepth 1 -not -name "*.tmpl" -type f)
        sed -i '/omxplayer/d' $(find docker/ -maxdepth 1 -not -name "*.tmpl" -type f)
        
        # Don't build the viewer container if we're on x86
        if [ "$container" == 'viewer' ]; then
            echo "Skipping viewer container for x86 builds..."
            continue
        fi
    fi

    docker "${DOCKER_BUILD_ARGS[@]}" \
        --cache-from "type=registry,ref=screenly/srly-ose-$container:$DOCKER_TAG" \
        --cache-from "type=local,src=/tmp/.buildx-cache" \
        --cache-to "type=local,dest=/tmp/.buildx-cache" \
        --platform "$TARGET_PLATFORM" \
        -f "docker/Dockerfile.$container" \
        -t "screenly/srly-ose-$container:latest" \
        -t "screenly/srly-ose-$container:$DOCKER_TAG" .

    # Push if the push flag is set and not cross compiling
    if [[ ( -n "${PUSH+x}" && -z "${CROSS_COMPILE+x}" ) ]]; then
        docker push "screenly/srly-ose-$container:$DOCKER_TAG"
    fi
done