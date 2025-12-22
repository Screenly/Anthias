#!/bin/bash

set -euo pipefail

DEBIAN_VERSION='bookworm'
QT_MAJOR='6'
QT_MINOR='4'
QT_PATCH='2'
QT_VERSION="${QT_MAJOR}.${QT_MINOR}.${QT_PATCH}"
CORE_COUNT="$(expr $(nproc) - 2)"
PLATFORM="${PLATFORM:-pi5}"
SUPPORTED_PLATFORMS=("pi4-64" "pi5")

if [[ ! " ${SUPPORTED_PLATFORMS[@]} " =~ " ${PLATFORM} " ]]; then
    echo "Unsupported platform: ${PLATFORM}"
    exit 1
fi

BOARD=${BOARD:-"x86"}
if [[ ! "${BOARD}" =~ ^(x86|pi5)$ ]]; then
    echo "Error: Invalid board specified. Must be either 'x86' or 'pi5'."
    exit 1
fi

function create_webview_archive() {
    local ARCHIVE_NAME="webview-${QT_VERSION}-${DEBIAN_VERSION}-${BOARD}-$GIT_HASH.tar.gz"
    local ARCHIVE_DESTINATION="/build/release/${ARCHIVE_NAME}"

    mkdir -p /build/release

    cp -rf /webview /build
    cd /build/webview
    qmake6
    make -j${CORE_COUNT}
    make install

    mkdir -p fakeroot/bin fakeroot/share/ScreenlyWebview
    mv ScreenlyWebview fakeroot/bin/
    cp -rf /webview/res fakeroot/share/ScreenlyWebview/

    cd fakeroot

    tar cfz ${ARCHIVE_DESTINATION} .
    cd /build/release
    sha256sum ${ARCHIVE_NAME} > ${ARCHIVE_DESTINATION}.sha256
}

create_webview_archive
