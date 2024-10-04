#!/bin/bash

set -euo pipefail

WEBVIEW_BUILDER_IMAGE_NAME='webview-builder'
WEBVIEW_BUILDER_CONTAINER_NAME='webview-builder-container'

BUILDX_ARGS=(
    '-f' 'docker/Dockerfile.qt6'
    '--platform' 'linux/arm/v7'
    '-t' "${WEBVIEW_BUILDER_IMAGE_NAME}"
)

RUN_ARGS=(
    '-itd'
    '--name' "${WEBVIEW_BUILDER_CONTAINER_NAME}"
    '-v' './build:/build'
    '-v' './src:/webview/src'
    '-v' './res:/webview/res'
    '-v' './CMakeLists.txt:/webview/CMakeLists.txt'
    '-v' './ScreenlyWebview.pro:/webview/ScreenlyWebview.pro'
    '-v' './scripts/build_webview.sh:/scripts/build_webview.sh'
    "${WEBVIEW_BUILDER_IMAGE_NAME}"
)

docker buildx build --load "${BUILDX_ARGS[@]}" .
docker rm -f "${WEBVIEW_BUILDER_CONTAINER_NAME}" || true
docker run "${RUN_ARGS[@]}" bash
