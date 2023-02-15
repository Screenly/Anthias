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

if [ -n "${CROSS_COMPILE+x}" ]; then
    echo "Running with cross-compile using docker buildx..."
    DOCKER_BUILD_ARGS=("buildx" "build" "--load" "--platform" "linux/arm/v6,linux/arm/v7,linux/arm/v8")

    echo 'Make sure you ran `docker run --privileged --rm tonistiigi/binfmt --install all` before the command'
else
    echo "Running without cross-compile..."
    docker buildx create --use
    DOCKER_BUILD_ARGS=("buildx" "build" "--load")
fi

if [ -n "${CLEAN_BUILD+x}" ]; then
    DOCKER_BUILD_ARGS+=("--no-cache")
fi

# Set various variables for the Dockerfiles to use
export BASE_IMAGE_TAG=buster
export DEBIAN_VERSION=buster
export QT_VERSION=5.15.2
export WEBVIEW_GIT_HASH=0b6d49359133246659b9ba1d8dd883e3fc5c9a91
export WEBVIEW_BASE_URL="https://github.com/Screenly/Anthias/releases/download/WebView-v0.2.1"

for pi_version in pi4 pi3 pi2 pi1; do
    if [ "$pi_version" == 'pi1' ]; then
        export BOARD="$pi_version"
        export BASE_IMAGE=balenalib/raspberry-pi
    elif [ "$pi_version" == 'pi2' ]; then
        export BOARD="$pi_version"
        export BASE_IMAGE=balenalib/raspberry-pi2
    elif [ "$pi_version" == 'pi3' ]; then
        export BOARD="$pi_version"
        export BASE_IMAGE=balenalib/raspberrypi3-debian
    elif [ "$pi_version" == 'pi4' ]; then
        # We want to restore once we've removed omxplayer as a dependency
        #export BASE_IMAGE=balenalib/raspberrypi4-64-debian
        export BOARD="$pi_version"
        export BASE_IMAGE=balenalib/raspberrypi3-debian
    fi

    for container in base server celery redis websocket nginx viewer wifi-connect; do
        echo "Building $container"
        cat "docker/Dockerfile.$container.tmpl" | envsubst > "docker/Dockerfile.$container" 

        docker "${DOCKER_BUILD_ARGS[@]}" \
            --build-arg "GIT_HASH=$GIT_HASH" \
            --build-arg "GIT_SHORT_HASH=$GIT_SHORT_HASH" \
            --build-arg "GIT_BRANCH=$GIT_BRANCH" \
            --build-arg "PI_VERSION=$pi_version" \
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
done
