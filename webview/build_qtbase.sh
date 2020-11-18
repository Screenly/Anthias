#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euo pipefail

QT_PI_1=/src/qt5/pi_1 # For Pi 1 and Pi Zero
QT_PI_2=/src/qt5/pi_2 # For Pi 2
QT_PI_3=/src/qt5/pi_3 # For Pi 3
BUILD_TARGET=/build
QT_BRANCH="5.9"
DEBIAN_VERSION=$(lsb_release -cs)

mkdir -p "$BUILD_TARGET"

echo "Building QT Base version $QT_BRANCH."

if [ ! -f "$BUILD_TARGET/qtbase-$QT_BRANCH-$DEBIAN_VERSION-pi1.tar.gz" ]; then
    echo "Building QT Base for Pi 1"
    mkdir -p "$QT_PI_1"
    cd "$QT_PI_1"
    git clone git://code.qt.io/qt/qtbase.git -b "$QT_BRANCH"
    cd qtbase
    ./configure \
        -release \
        -opengl es2 \
        -device linux-rasp-pi-g++ \
        -device-option CROSS_COMPILE=/usr/bin/ \
        -opensource \
        -confirm-license \
        -make libs \
        -prefix /usr/local/qt5pi \
        -extprefix $QT_PI_1/qt5pi \
        -no-use-gold-linker

    make -j "$(nproc --all)"
    make install
    cp -r /usr/share/fonts/truetype/dejavu/ "$QT_PI_1/qt5pi/lib/fonts"
    cd "$QT_PI_1"
    tar -zcvf "$BUILD_TARGET/qtbase-$QT_BRANCH-$DEBIAN_VERSION-pi1.tar.gz" qt5pi
    cd "$BUILD_TARGET"
    sha256sum "qtbase-$QT_BRANCH-$DEBIAN_VERSION-pi1.tar.gz" > "qtbase-$QT_BRANCH-$DEBIAN_VERSION-pi1.tar.gz.sha256"
fi

if [ ! -f "$BUILD_TARGET/qtbase-$QT_BRANCH-$DEBIAN_VERSION-pi2.tar.gz" ]; then
    echo "Building QT Base for Pi 2"
    mkdir -p "$QT_PI_2"
    cd "$QT_PI_2"
    git clone git://code.qt.io/qt/qtbase.git -b "$QT_BRANCH"
    cd qtbase
    ./configure \
        -release \
        -opengl es2 \
        -device linux-rasp-pi2-g++ \
        -device-option CROSS_COMPILE=/usr/bin/ \
        -opensource \
        -confirm-license \
        -make libs \
        -prefix /usr/local/qt5pi \
        -extprefix "$QT_PI_2/qt5pi" \
        -no-use-gold-linker

    make -j "$(nproc --all)"
    make install
    cp -r /usr/share/fonts/truetype/dejavu/ "$QT_PI_2/qt5pi/lib/fonts"
    cd "$QT_PI_2"
    tar -zcvf "$BUILD_TARGET/qtbase-$QT_BRANCH-$DEBIAN_VERSION-pi2.tar.gz" qt5pi
    cd "$BUILD_TARGET"
    sha256sum "qtbase-$QT_BRANCH-$DEBIAN_VERSION-pi2.tar.gz" > "qtbase-$QT_BRANCH-$DEBIAN_VERSION-pi2.tar.gz.sha256"
fi

if [ ! -f "$BUILD_TARGET/qtbase-$QT_BRANCH-$DEBIAN_VERSION-pi3.tar.gz" ]; then
    echo "Building QT Base for Pi 3"
    mkdir -p $QT_PI_3
    cd "$QT_PI_3"
    git clone git://code.qt.io/qt/qtbase.git -b "$QT_BRANCH"
    cd qtbase
    ./configure \
        -release \
        -opengl es2 \
        -device linux-rasp-pi3-g++ \
        -device-option CROSS_COMPILE=/usr/bin/ \
        -opensource \
        -confirm-license \
        -release \
        -make libs \
        -prefix /usr/local/qt5pi \
        -extprefix "$QT_PI_3/qt5pi" \
        -no-use-gold-linker

    make -j "$(nproc --all)"
    make install
    cp -r /usr/share/fonts/truetype/dejavu/ "$QT_PI_3/qt5pi/lib/fonts"
    cd "$QT_PI_3"
    tar -zcvf "$BUILD_TARGET/qtbase-$QT_BRANCH-$DEBIAN_VERSION-pi3.tar.gz" qt5pi
    cd "$BUILD_TARGET"
    sha256sum "qtbase-$QT_BRANCH-$DEBIAN_VERSION-pi3.tar.gz" > "qtbase-$QT_BRANCH-$DEBIAN_VERSION-pi3.tar.gz.sha256"
fi
