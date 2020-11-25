#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euo pipefail

BUILD_TARGET=/build
QT_BRANCH="5.15"
DEBIAN_VERSION=$(lsb_release -cs)

mkdir -p "$BUILD_TARGET"

echo "Building QT Base version $QT_BRANCH."

function build_qtwebengine () {
    if [ ! -f "$BUILD_TARGET/qtwebengine-$QT_BRANCH-$DEBIAN_VERSION.tar.gz" ]; then
        SRC_DIR="/src/qtwebengine"
        git clone git://code.qt.io/qt/qtwebengine.git -b "$QT_BRANCH" "$SRC_DIR"
        cd "$SRC_DIR"
        git submodule init
        git submodule update
        /usr/lib/arm-linux-gnueabihf/qt5/bin/qmake
        make -j "$(nproc --all)"
        make install
    fi
}

function build_qtbase () {
    if [ ! -f "$BUILD_TARGET/qtbase-$QT_BRANCH-$DEBIAN_VERSION-$1.tar.gz" ]; then
        SRC_DIR="/src/$1"
        echo "Building QT Base for $1"
        mkdir -p "$SRC_DIR"
        cd "$SRC_DIR"
        git clone git://code.qt.io/qt/qtbase.git -b "$QT_BRANCH"
        cd qtbase
        ./configure \
            -release \
            -no-compile-examples \
            -opengl es2 \
            -device "linux-rasp-$1-g++" \
            -device-option CROSS_COMPILE=/usr/bin/ \
            -opensource \
            -confirm-license \
            -make libs \
            -prefix /usr/local/qt5pi \
            -extprefix "$SRC_DIR/qt5pi" \
            -no-use-gold-linker

        make -j "$(nproc --all)"
        make install
        cp -r /usr/share/fonts/truetype/dejavu/ "$SRC_DIR/qt5pi/lib/fonts"
        cd "$SRC_DIR"
        tar -zcvf "$BUILD_TARGET/qtbase-$QT_BRANCH-$DEBIAN_VERSION-$1.tar.gz" qt5pi
        cd "$BUILD_TARGET"
        sha256sum "qtbase-$QT_BRANCH-$DEBIAN_VERSION-$1.tar.gz" > "qtbase-$QT_BRANCH-$DEBIAN_VERSION-$1.tar.gz.sha256"
    else
        echo "Build already exist."
    fi
}

build_qtwebengine
build_qtbase pi
build_qtbase pi2
build_qtbase pi3

# We can probably refactor the other `build_qtbase` function to include these
# unique build options, but this will do for now even if it isn't DRY.
if [ ! -f "$BUILD_TARGET/qtbase-$QT_BRANCH-$DEBIAN_VERSION-pi4.tar.gz" ]; then
    echo "Building QT Base for Pi 4"
    SRC_DIR="/src/pi4"
    mkdir -p "$SRC_DIR"
    cd "$SRC_DIR"
    git clone git://code.qt.io/qt/qtbase.git -b "$QT_BRANCH"
    cd qtbase
    ./configure \
        -release \
        -no-compile-examples \
        -device linux-rasp-pi4-v3d-g++ \
        -device-option CROSS_COMPILE=/usr/bin/ \
        -opensource \
        -confirm-license \
        -release \
        -make libs \
        -prefix /usr/local/qt5pi \
        -extprefix "$SRC_DIR/qt5pi" \
        -no-use-gold-linker

    make -j "$(nproc --all)"
    make install
    cp -r /usr/share/fonts/truetype/dejavu/ "$SRC_DIR/qt5pi/lib/fonts"
    cd "$SRC_DIR"
    tar -zcvf "$BUILD_TARGET/qtbase-$QT_BRANCH-$DEBIAN_VERSION-pi4.tar.gz" qt5pi
    cd "$BUILD_TARGET"
    sha256sum "qtbase-$QT_BRANCH-$DEBIAN_VERSION-pi4.tar.gz" > "qtbase-$QT_BRANCH-$DEBIAN_VERSION-pi4.tar.gz.sha256"
fi
