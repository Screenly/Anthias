#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euo pipefail

GITBRANCH=$(git rev-parse --abbrev-ref HEAD)

if [ -n "${CROSS_COMPILE+x}" ]; then
    echo "Running with cross-compile using docker buildx..."
    DOCKER_BUILD_ARGS=("buildx" "build" "--push" "--platform" "linux/arm/v6,linux/arm/v7")
else
    echo "Running without cross-compile..."
    DOCKER_BUILD_ARGS=("build")
fi

for container in base server celery redis websocket; do
    echo "Building $container"
    docker "${DOCKER_BUILD_ARGS[@]}" \
        -f "docker/Dockerfile.$container" \
        -t "screenly/srly-ose-$container:$GITBRANCH" .

    # Push if the push flag is set and not cross compiling
    if [[ ( -n "${PUSH+x}" && -z "${CROSS_COMPILE+x}" ) ]]; then
        docker push "screenly/srly-ose-$container:$GITBRANCH"
    fi
done

echo "Building viewer for different architectures..."
for pi_version in pi1 pi2 pi3; do
    echo "Building viewer container for $pi_version"
    docker "${DOCKER_BUILD_ARGS[@]}" \
        --build-arg "PI_VERSION=$pi_version" \
        -f docker/Dockerfile.viewer \
        -t "screenly/srly-ose-viewer:$GITBRANCH-$pi_version" .

    # Push if the push flag is set and not cross compiling
    if [[ ( -n "${PUSH+x}" && -z "${CROSS_COMPILE+x}" ) ]]; then
        docker push "screenly/srly-ose-viewer:$GITBRANCH-$pi_version"
    fi
done

