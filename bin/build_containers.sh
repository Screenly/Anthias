#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euox pipefail

# Set various confirguration variables for the Dockerfiles to use
export GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
export GIT_SHORT_HASH=$(git rev-parse --short HEAD)
export GIT_HASH=$(git rev-parse HEAD)
export BASE_IMAGE_TAG=buster
export DEBIAN_VERSION=buster
export QT_VERSION=5.15.2
export WEBVIEW_GIT_HASH=0b6d49359133246659b9ba1d8dd883e3fc5c9a91
export WEBVIEW_BASE_URL="https://github.com/Screenly/Anthias/releases/download/WebView-v0.2.1"
export CHROME_DL_URL="https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_107.0.5304.121-1_amd64.deb"
export CHROMEDRIVER_DL_URL="https://chromedriver.storage.googleapis.com/107.0.5304.62/chromedriver_linux64.zip"

# Handle command-line arguments.
declare -a SKIP_SERVICES
declare -a VALID_SERVICES=(
    server
    celery
    redis
    websocket
    nginx
    viewer
    wifi-connect
    'test'
)
declare -a SERVICES=("${VALID_SERVICES[@]}")

function usage() {
    echo "Usage: $0 [options]"
    echo "Options:"

    echo "  --skip-<service>  Skip building the specified service"
    echo -n "                    Valid services: "
    for service in "${VALID_SERVICES[@]}"; do
        echo -n "$service "
    done
    echo

    echo "  -h, --help        Show this help message"
}

function is_valid_service() {
    local service="$1"
    for valid_service in "${VALID_SERVICES[@]}"; do
        if [ "$service" == "$valid_service" ]; then
            return 0
        fi
    done
    return 1
}

while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        --skip-*)
            SERVICE="${key#--skip-}"

            if ! is_valid_service "$SERVICE"; then
                echo "Invalid service: $SERVICE"
                usage
                exit 1
            fi

            for i in "${!SERVICES[@]}"; do
                if [ "${SERVICES[$i]}" == "$SERVICE" ]; then
                    unset "SERVICES[$i]"
                fi
            done

            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $key"
            usage
            exit 1
            ;;
    esac
done

DOCKER_BUILD_ARGS=("buildx" "build" "--load")
echo 'Make sure you ran `docker buildx create --use` before the command'

if [ -n "${CLEAN_BUILD+x}" ]; then
    DOCKER_BUILD_ARGS+=("--no-cache")
fi

# Detect what platform
if [ ! -f /proc/device-tree/model ] && [ -z "${BUILD_TARGET+x}" ]; then
    export BOARD="x86"
    export BASE_IMAGE=debian
    export TARGET_PLATFORM=linux/amd64
elif grep -qF "Raspberry Pi 4" /proc/device-tree/model || [ "${BUILD_TARGET}" == 'pi4' ]; then
    export BASE_IMAGE=balenalib/raspberrypi3-debian
    export BOARD="pi4"
    export TARGET_PLATFORM=linux/arm/v8
elif grep -qF "Raspberry Pi 3" /proc/device-tree/model || [ "${BUILD_TARGET}" == 'pi3' ]; then
    export BOARD="pi3"
    export BASE_IMAGE=balenalib/raspberrypi3-debian
    export TARGET_PLATFORM=linux/arm/v7
elif grep -qF "Raspberry Pi 2" /proc/device-tree/model || [ "${BUILD_TARGET}" == 'pi2' ]; then
    export BOARD="pi2"
    export BASE_IMAGE=balenalib/raspberry-pi2
    export TARGET_PLATFORM=linux/arm/v6
elif grep -qF "Raspberry Pi 1" /proc/device-tree/model || [ "${BUILD_TARGET}" == 'pi1' ]; then
    export BOARD="pi1"
    export BASE_IMAGE=balenalib/raspberry-pi
    export TARGET_PLATFORM=linux/arm/v6
fi

if [ "$GIT_BRANCH" = "master" ]; then
    export DOCKER_TAG="latest-$BOARD"
else
    export DOCKER_TAG="$GIT_BRANCH-$BOARD"
fi

for container in ${SERVICES[@]}; do
    echo "Building $container"

    # For all but redis and nginx, and viewer append the base layer
    if [ ! "$container" == 'redis' ] || [ ! "$container" == 'nginx' ] || [ ! "$container" == 'viewer' ]; then
        cat "docker/Dockerfile.base.tmpl" | envsubst > "docker/Dockerfile.$container"
        cat "docker/Dockerfile.$container.tmpl" | envsubst >> "docker/Dockerfile.$container"
    else
        cat "docker/Dockerfile.$container.tmpl" | envsubst > "docker/Dockerfile.$container"
    fi

    # If we're running on x86, remove all Pi specific packages
    if [ "$BOARD" == 'x86' ]; then
        sed -i '/libraspberrypi0/d' $(find docker/ -maxdepth 1 -not -name "*.tmpl" -type f)
        sed -i '/omxplayer/d' $(find docker/ -maxdepth 1 -not -name "*.tmpl" -type f)

        # Don't build the viewer container if we're on x86
        if [ "$container" == 'viewer' ]; then
            echo "Skipping viewer container for x86 builds..."
            continue
        fi
    else
        if [ "$container" == 'test' ]; then
            echo "Skipping test container for Pi builds..."
            continue
        fi
    fi

    docker "${DOCKER_BUILD_ARGS[@]}" \
        --cache-from "type=local,src=/tmp/.buildx-cache" \
        --cache-to "type=local,dest=/tmp/.buildx-cache" \
        --platform "$TARGET_PLATFORM" \
        -f "docker/Dockerfile.$container" \
        -t "screenly/srly-ose-$container:latest" \
        -t "screenly/anthias-$container:latest" \
        -t "anthias-$container:latest" \
        -t "screenly/anthias-$container:$DOCKER_TAG" \
        -t "screenly/srly-ose-$container:$DOCKER_TAG" .

    # Push if the push flag is set and not cross compiling
    if [[ ( -n "${PUSH+x}" && -z "${CROSS_COMPILE+x}" ) ]]; then
        docker push "screenly/srly-ose-$container:$DOCKER_TAG"
        docker push "screenly/anthias-$container:$DOCKER_TAG"
    fi
done
