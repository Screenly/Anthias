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
WEBVIEW_VERSION="0.3.5"

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

    WEBVIEW_DL_URL="$ANTHIAS_RELEASE_URL/download/WebView-v$WEBVIEW_VERSION/qt5-$QT_VERSION-$DEBIAN_VERSION-$DEVICE.tar.gz"
    WEBVIEW_DL_URL_SHA256="$WEBVIEW_DL_URL.sha256"

    curl -sL "$WEBVIEW_DL_URL" -o /tmp/qt5-$QT_VERSION-$DEBIAN_VERSION-$DEVICE.tar.gz
    curl -sL "$WEBVIEW_DL_URL_SHA256" -o /tmp/qt5-$QT_VERSION-$DEBIAN_VERSION-$DEVICE.tar.gz.sha256

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

    mkdir -p fakeroot/bin fakeroot/share/ScreenlyWebview
    mv ScreenlyWebview fakeroot/bin/
    cp -rf /webview/res fakeroot/share/ScreenlyWebview/

    pushd fakeroot
    tar cfz "$BUILD_TARGET/webview-$QT_VERSION-$DEBIAN_VERSION-$1-$GIT_HASH.tar.gz" .
    popd

    pushd "$BUILD_TARGET"
    sha256sum "webview-$QT_VERSION-$DEBIAN_VERSION-$1-$GIT_HASH.tar.gz" > "webview-$QT_VERSION-$DEBIAN_VERSION-$1-$GIT_HASH.tar.gz.sha256"
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

