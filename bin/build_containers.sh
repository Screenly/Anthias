#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euo pipefail

#GITBRANCH=$(git rev-parse --abbrev-ref HEAD)
GITBRANCH='latest'

if [ -n "$CROSS_COMPILE" ]; then
    echo "Running with cross-compile using docker buildx..."
    DOCKER_BUILD_ARGS=("buildx" "build" "--push" "--platform" "linux/arm/v6,linux/arm/v7")
else
    echo "Running without cross-compile..."
    DOCKER_BUILD_ARGS=("build")
fi


for container in server celery redis websocket; do
    echo "Building $container"
    docker "${DOCKER_BUILD_ARGS[@]}" \
        -f "docker/Dockerfile.$container" \
        -t "srly-ose-$container:$GITBRANCH" .
done

echo "Building viewer for different architectures..."
for pi_version in pi1 pi2 pi3; do
    docker "${DOCKER_BUILD_ARGS[@]}" \
        --build-arg "PI_VERSION=$pi_version" \
        -f docker/Dockerfile.viewer \
        -t "srly-ose-viewer:$GITBRANCH-$pi_version" .
done

