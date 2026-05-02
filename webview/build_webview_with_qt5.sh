#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -exuo pipefail

BUILD_TARGET=/build
SRC=/src
QT_MAJOR="5"
QT_MINOR="15"
QT_BUG_FIX="14"
QT_VERSION="$QT_MAJOR.$QT_MINOR.$QT_BUG_FIX"
DEBIAN_VERSION=$(lsb_release -cs)
MAKE_CORES="$(expr $(nproc) + 2)"

ANTHIAS_RELEASE_URL="https://github.com/Screenly/Anthias/releases"
# Pre-built Qt 5 cross-compile toolchain published under this WebView
# release tag. Pinned independently of the current WEBVIEW_VERSION since
# the toolchain itself doesn't change between WebView releases.
QT5_TOOLCHAIN_TAG="WebView-v2026.04.1"

# WEBVIEW_VERSION is the CalVer release identifier (YYYY.MM.PATCH).
# CI extracts it from the WebView-v* tag; for local builds the caller
# can set it explicitly, otherwise we fall back to a date-stamped dev
# version so the artifact filename is still well-formed.
WEBVIEW_VERSION="${WEBVIEW_VERSION:-$(date -u +%Y.%m).0-dev}"

mkdir -p "$BUILD_TARGET"
mkdir -p "$SRC"

function fetch_cross_compile_tool () {
    # The Raspberry Pi Foundation's cross compiling tools are too old so we need newer ones.
    # References:
    # * https://github.com/UvinduW/Cross-Compiling-Qt-for-Raspberry-Pi-4
    # * https://releases.linaro.org/components/toolchain/binaries/latest-7/armv8l-linux-gnueabihf/
    if [ ! -d "/src/gcc-linaro-7.4.1-2019.02-x86_64_arm-linux-gnueabihf" ]; then
        pushd /src/
        wget -q https://releases.linaro.org/components/toolchain/binaries/7.4-2019.02/arm-linux-gnueabihf/gcc-linaro-7.4.1-2019.02-x86_64_arm-linux-gnueabihf.tar.xz
        tar xf gcc-linaro-7.4.1-2019.02-x86_64_arm-linux-gnueabihf.tar.xz
        rm gcc-linaro-7.4.1-2019.02-x86_64_arm-linux-gnueabihf.tar.xz
        popd
    fi
}

function download_and_extract_qt5() {
    local SRC_DIR="$1"
    local DEVICE="$2"

    WEBVIEW_DL_URL="$ANTHIAS_RELEASE_URL/download/$QT5_TOOLCHAIN_TAG/qt5-$QT_VERSION-$DEBIAN_VERSION-$DEVICE.tar.gz"
    WEBVIEW_DL_URL_SHA256="$WEBVIEW_DL_URL.sha256"

    if [ ! -f /tmp/qt5-$QT_VERSION-$DEBIAN_VERSION-$DEVICE.tar.gz ]; then
        curl -sL "$WEBVIEW_DL_URL" -o /tmp/qt5-$QT_VERSION-$DEBIAN_VERSION-$DEVICE.tar.gz
    fi

    if [ ! -f /tmp/qt5-$QT_VERSION-$DEBIAN_VERSION-$DEVICE.tar.gz.sha256 ]; then
        curl -sL "$WEBVIEW_DL_URL_SHA256" -o /tmp/qt5-$QT_VERSION-$DEBIAN_VERSION-$DEVICE.tar.gz.sha256
    fi

    cp -n /tmp/qt5-$QT_VERSION-$DEBIAN_VERSION-$DEVICE.tar.gz /build/
    cp -n /tmp/qt5-$QT_VERSION-$DEBIAN_VERSION-$DEVICE.tar.gz.sha256 /build/

    cd /tmp
    sha256sum -c "qt5-$QT_VERSION-$DEBIAN_VERSION-$DEVICE.tar.gz.sha256"
    tar -xzf "qt5-$QT_VERSION-$DEBIAN_VERSION-$DEVICE.tar.gz" -C "$SRC_DIR"
}

function build_qt () {
    # This build process is inspired by
    # https://www.tal.org/tutorials/building-qt-512-raspberry-pi
    local SRC_DIR="/src/$1"
    mkdir -p "$SRC_DIR"/qt${QT_MAJOR}pi

    download_and_extract_qt5 "$SRC_DIR" "$1"

    cp -rf /webview "$SRC_DIR/"

    pushd "$SRC_DIR/webview"

    "$SRC_DIR/qt${QT_MAJOR}pi/bin/qmake"
    make -j"$MAKE_CORES"
    make install

    mkdir -p fakeroot/bin fakeroot/share/AnthiasWebview
    mv AnthiasWebview fakeroot/bin/
    cp -rf /webview/res fakeroot/share/AnthiasWebview/

    local ARCHIVE="webview-$WEBVIEW_VERSION-$DEBIAN_VERSION-$1.tar.gz"

    pushd fakeroot
    tar cfz "$BUILD_TARGET/$ARCHIVE" .
    popd

    pushd "$BUILD_TARGET"
    sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
    popd
}

fetch_cross_compile_tool

if [ ! "${TARGET-}" ]; then
    # Let's work our way through all Pis in order of relevance
    for device in pi4 pi3 pi2 pi1; do
        build_qt "$device"
    done
else
    build_qt "$TARGET"
fi

