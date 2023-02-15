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
    DOCKER_BUILD_ARGS=("buildx" "build" "--load" "--platform" "linux/arm/v6,linux/arm/v7")

    echo 'Make sure you ran `docker run --privileged --rm tonistiigi/binfmt --install all` before the command'
else
    echo "Running without cross-compile..."
    DOCKER_BUILD_ARGS=("build")
fi

if [ -n "${CLEAN_BUILD+x}" ]; then
    DOCKER_BUILD_ARGS+=("--no-cache")
fi


export BASE_IMAGE_TAG=buster

for pi_version in pi4 pi3 pi2 pi1; do
    if [ "$pi_version" == 'pi1' ]; then
        export BASE_IMAGE=balenalib/raspberry-pi
    elif [ "$pi_version" == 'pi2' ]; then
        export BASE_IMAGE=balenalib/raspberry-pi2
    elif [ "$pi_version" == 'pi3' ]; then
        export BASE_IMAGE=raspberrypi3-debian
    elif [ "$pi_version" == 'pi4' ]; then
        export BASE_IMAGE=raspberrypi4-64-debian
    fi

    # Perform substitutions
    cat docker/Dockerfile.base | envsubst > docker/Dockerfile.base 
    cat docker/Dockerfile.viewer | envsubst > docker/Dockerfile.viewer

    for container in base server celery redis websocket nginx viewer wifi-connect; do
        echo "Building $container"
        docker "${DOCKER_BUILD_ARGS[@]}" \
            --build-arg "GIT_HASH=$GIT_HASH" \
            --build-arg "GIT_SHORT_HASH=$GIT_SHORT_HASH" \
            --build-arg "GIT_BRANCH=$GIT_BRANCH" \
            --build-arg "PI_VERSION=$pi_version" \
            -f "docker/Dockerfile.$container" \
            -t "screenly/srly-ose-$container:$DOCKER_TAG" .

        # Push if the push flag is set and not cross compiling
        if [[ ( -n "${PUSH+x}" && -z "${CROSS_COMPILE+x}" ) ]]; then
            docker push "screenly/srly-ose-$container:$DOCKER_TAG-$pi_version"
        fi
    done
done
