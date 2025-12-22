#!/bin/bash

set -euo pipefail

BUILDER_DOCKERFILE='docker/Dockerfile.dev'
BUILDER_IMAGE_NAME='anthias-dockerfile-image-builder'
BUILDER_CONTAINER_NAME="${BUILDER_IMAGE_NAME}-instance"
BUILD_TARGET="${BUILD_TARGET:-x86}"
ENVIRONMENT="${ENVIRONMENT:-development}"

docker build \
    --pull \
    -f "$BUILDER_DOCKERFILE" \
    -t "$BUILDER_IMAGE_NAME" .

docker rm -f "$BUILDER_CONTAINER_NAME" || true
docker run \
    --rm \
    --name="$BUILDER_CONTAINER_NAME" \
    -v "$(pwd):/app" \
    -v "${BUILDER_IMAGE_NAME}-venv:/app/.venv" \
    "$BUILDER_IMAGE_NAME" \
    uv run python -m tools.image_builder \
        --environment="$ENVIRONMENT" \
        --dockerfiles-only \
        --disable-cache-mounts \
        --build-target="$BUILD_TARGET"
