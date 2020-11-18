#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euo pipefail

docker-compose build

echo "Building viewer for different architectures..."
for pi_version in pi1 pi2 pi3; do
    docker build \
        --build-arg "PI_VERSION=$pi_version" \
        -f docker/Dockerfile.viewer \
        -t "srly-ose-viewer:$pi_version" .
done

