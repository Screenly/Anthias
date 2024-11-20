#!/bin/bash

set -euo pipefail

CORE_COUNT="$(nproc)"

function build_webview() {
    local ARCHIVE_NAME="webview.tar.gz"
    local ARCHIVE_DESTINATION="/build/${ARCHIVE_NAME}"

    rsync -aP /webview /build
    cd /build/webview
    mkdir -p build && cd build
    cmake ..
    cmake --build . --parallel "${CORE_COUNT}"

    mkdir -p fakeroot/bin fakeroot/share/ScreenlyWebview
    mv ScreenlyWebview fakeroot/bin/
    cp -rf ../res fakeroot/share/ScreenlyWebview/

    cd fakeroot

    tar cfz ${ARCHIVE_DESTINATION} .
}

function main() {
    local RELEASE_DIR='/build/release'
    mkdir -p ${RELEASE_DIR}

    build_webview
}

main
