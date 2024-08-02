#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euox pipefail

# Set various confirguration variables for the Dockerfiles to use
export GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
export GIT_SHORT_HASH=$(git rev-parse --short HEAD)
export GIT_HASH=$(git rev-parse HEAD)
export BASE_IMAGE_TAG=bookworm
export DEBIAN_VERSION=bookworm
export ENVIRONMENT=${ENVIRONMENT:-production}

declare -a SERVICES=(
    server
    celery
    redis
    websocket
    nginx
    viewer
    wifi-connect
    'test'
)

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
    echo "Building $container..."

    uppercase_container=$(
        echo $container | tr '[:lower:]' '[:upper:]' | tr '-' '_'
    )
    skip_variable="SKIP_${uppercase_container}"

    if [ -n "${!skip_variable:-}" ]; then
        echo "$skip_variable is set. Skipping $container..."
        continue
    fi

    if [ "$container" == 'viewer' ]; then
        export QT_VERSION=5.15.14
        export WEBVIEW_GIT_HASH=4bd295c4a1197a226d537938e947773f4911ca24
        export WEBVIEW_BASE_URL="https://github.com/Screenly/Anthias/releases/download/WebView-v0.3.1"
    elif [ "$container" == 'test' ]; then
        export CHROME_DL_URL="https://storage.googleapis.com/chrome-for-testing-public/123.0.6312.86/linux64/chrome-linux64.zip"
        export CHROMEDRIVER_DL_URL="https://storage.googleapis.com/chrome-for-testing-public/123.0.6312.86/linux64/chromedriver-linux64.zip"
    elif [ "$container" == 'wifi-connect' ]; then
        # We don't support wifi-connect on x86 yet.
        if [ "$BOARD" == 'x86' ]; then
            continue
        fi

        # Logic for determining the correct architecture for the wifi-connect container
        if [ "$TARGET_PLATFORM" = 'linux/arm/v6' ]; then
            architecture=rpi
        else
            architecture=armv7hf
        fi

        wc_download_url='https://api.github.com/repos/balena-os/wifi-connect/releases/93025295'
        jq_filter=".assets[] | select (.name|test(\"linux-$architecture\")) | .browser_download_url"
        archive_url=$(curl -sL "$wc_download_url" | jq -r "$jq_filter")
        export ARCHIVE_URL="$archive_url"
    fi

    # For all but redis and nginx, and viewer append the base layer
    if [[ ! "$container" =~ ^(redis|nginx|viewer)$ ]]; then
        cat "docker/Dockerfile.base.tmpl" | envsubst > "docker/Dockerfile.$container"
        cat "docker/Dockerfile.$container.tmpl" | envsubst >> "docker/Dockerfile.$container"
    else
        cat "docker/Dockerfile.$container.tmpl" | envsubst > "docker/Dockerfile.$container"
    fi

    # If we're running on x86, remove all Pi specific packages
    if [ "$BOARD" == 'x86' ]; then
        if [[ $OSTYPE == 'darwin'* ]]; then
            SED_ARGS=(-i "")
        else
            SED_ARGS=(-i)
        fi

        sed "${SED_ARGS[@]}" -e '/libraspberrypi0/d' $(find docker/ -maxdepth 1 -not -name "*.tmpl" -type f)

        # Don't build the viewer container if we're on x86
        if [ "$container" == 'viewer' ]; then
            echo "Skipping viewer container for x86 builds..."
            continue
        fi
    else
        if [ "$BOARD" == "pi1" ] && [ "$container" == "viewer" ]; then
            # Remove the libssl1.1 from Dockerfile.viewer
            sed -i '/libssl1.1/d' "docker/Dockerfile.$container"
        fi

        if [ "$container" == 'test' ]; then
            echo "Skipping test container for Pi builds..."
            continue
        fi
    fi

    if [[ -n "${DEV_MODE:-}" ]] && [[ "${DEV_MODE}" -ne 0 ]]; then
        sed -i 's/RUN --mount.\+ /RUN /g' "docker/Dockerfile.$container"
    fi

    if [[ -n "${DOCKERFILES_ONLY:-}" ]] && [[ "${DOCKERFILES_ONLY}" -ne 0 ]]; then
        echo "Variable DOCKERFILES_ONLY is set. Skipping build for $container..."
        continue
    fi

    PUSH_ARGS=()
    BUILDX_ARGS=()

    if [ "$GIT_BRANCH" = "experimental" ]; then
        PUSH_ARGS+=(
            "screenly/anthias-$container:experimental-$BOARD"
            "screenly/anthias-$container:experimental-$GIT_SHORT_HASH-$BOARD"
        )
    else
        PUSH_ARGS+=(
            "screenly/anthias-$container:$DOCKER_TAG"
            "screenly/anthias-$container:$GIT_SHORT_HASH-$BOARD"
            "screenly/srly-ose-$container:$DOCKER_TAG"
            "screenly/srly-ose-$container:$GIT_SHORT_HASH-$BOARD"
        )
    fi

    for tag in "${PUSH_ARGS[@]}"; do
        BUILDX_ARGS+=("-t" "$tag")
    done

    docker "${DOCKER_BUILD_ARGS[@]}" \
        --cache-from "type=local,src=/tmp/.buildx-cache" \
        --cache-to "type=local,dest=/tmp/.buildx-cache" \
        --platform "$TARGET_PLATFORM" \
        -f "docker/Dockerfile.$container" \
        "${BUILDX_ARGS[@]}" .

    # Push if the push flag is set and not cross compiling.
    if [[ ( -n "${PUSH+x}" && -z "${CROSS_COMPILE+x}" ) ]]; then
        for tag in "${PUSH_ARGS[@]}"; do
            docker push "$tag"
        done
    fi
done
