#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -exuo pipefail

BUILD_TARGET=/build
SRC=/src
QT_BRANCH="5.15.2"
DEBIAN_VERSION=$(lsb_release -cs)

mkdir -p "$BUILD_TARGET"
mkdir -p "$SRC"

/usr/games/cowsay -f tux "Building QT Base version $QT_BRANCH."
if [ "${BUILD_WEBENGINE-x}" == "1" ]; then
    /usr/games/cowsay -f tux "...with QTWebEngine."
fi

function fetch_cross_compile_tool () {
    # The Raspberry Pi Foundations cross compiling tools are too old so we need newer ones
    # https://github.com/UvinduW/Cross-Compiling-Qt-for-Raspberry-Pi-4
    # https://releases.linaro.org/components/toolchain/binaries/latest-7/armv8l-linux-gnueabihf/
    if [ ! -d "/src/gcc-linaro-7.4.1-2019.02-x86_64_arm-linux-gnueabihf" ]; then
        cd /src/
        wget -q https://releases.linaro.org/components/toolchain/binaries/7.4-2019.02/arm-linux-gnueabihf/gcc-linaro-7.4.1-2019.02-x86_64_arm-linux-gnueabihf.tar.xz
        tar xf gcc-linaro-7.4.1-2019.02-x86_64_arm-linux-gnueabihf.tar.xz
        rm gcc-linaro-7.4.1-2019.02-x86_64_arm-linux-gnueabihf.tar.xz
    fi
}

function fetch_rpi_firmware () {
    if [ ! -d "/src/opt" ]; then
        cd /src

        # We do an `svn checkout` here as the entire git repo here is *huge*
        # and `git` doesn't  support partial checkouts well (yet)
        svn checkout -q https://github.com/raspberrypi/firmware/trunk/opt
    fi
    rsync -aqP /src/opt/ /sysroot/opt/
}

function fetch_qt () {
    local SRC_DIR="/src/qtbase"
    if [ ! -d "$SRC_DIR" ]; then
        git clone git://code.qt.io/qt/qtbase.git -b "$QT_BRANCH" "$SRC_DIR"
    else
        cd "$SRC_DIR"
        git reset --hard
        git clean -dfx
    fi
}

function fetch_qtdeclarative () {
    local SRC_DIR="/src/qtdeclarative"
    if [ ! -d "$SRC_DIR" ]; then
        git clone git://code.qt.io/qt/qtdeclarative.git -b "$QT_BRANCH" "$SRC_DIR"
        cd "$SRC_DIR"
        git submodule init
        git submodule update

    else
        cd "$SRC_DIR"
        git reset --hard
        git clean -dfx
    fi
}

function fetch_qtwebengine () {
    local SRC_DIR="/src/qtwebengine"
    if [ ! -d "$SRC_DIR" ]; then
        git clone git://code.qt.io/qt/qtwebengine.git -b "$QT_BRANCH" "$SRC_DIR"
        cd "$SRC_DIR"
        git submodule init
        git submodule update

    else
        cd "$SRC_DIR"
        git reset --hard
        git clean -dfx
    fi

    # Patch up WebEngine due to GCC bug
    # https://www.enricozini.org/blog/2020/qt5/build-qt5-cross-builder-with-raspbian-sysroot-compiling-with-the-sysroot/
    cd "$SRC_DIR"
    sed -i '1s/^/#pragma GCC push_options\n#pragma GCC optimize ("O0")\n/' src/3rdparty/chromium/third_party/skia/third_party/skcms/skcms.cc
    echo "#pragma GCC pop_options" >> src/3rdparty/chromium/third_party/skia/third_party/skcms/skcms.cc
}

function build_qtbase () {
    # This build process is inspired by
    # https://www.tal.org/tutorials/building-qt-512-raspberry-pi
    local SRC_DIR="/src/$1"

    if [ ! -f "$BUILD_TARGET/qtbase-$QT_BRANCH-$DEBIAN_VERSION-$1.tar.gz" ]; then
        /usr/games/cowsay -f tux "Building QT Base for $1"
        mkdir -p "$SRC_DIR"
        cd "$SRC_DIR"

        if [ "$1" = "pi1" ]; then
            local BUILD_ARGS=(
                "-device" "linux-rasp-pi-g++"
            )
        elif [ "$1" = "pi2" ]; then
            local BUILD_ARGS=(
                "-device" "linux-rasp-pi2-g++"
            )
        elif [ "$1" = "pi3" ]; then
            local BUILD_ARGS=(
                "-device" "linux-rasp-pi3-g++"
            )
        elif [ "$1" = "pi4" ]; then
            local BUILD_ARGS=(
                "-device" "linux-rasp-pi4-v3d-g++"
            )
        else
            echo "Unknown device. Exiting."
            exit 1
        fi

        /src/qtbase/configure \
            "${BUILD_ARGS[@]}" \
            -confirm-license \
            -dbus-linked \
            -device-option CROSS_COMPILE=/src/gcc-linaro-7.4.1-2019.02-x86_64_arm-linux-gnueabihf/bin/arm-linux-gnueabihf- \
            -eglfs \
            -evdev \
            -extprefix "$SRC_DIR/qt5pi" \
            -force-pkg-config \
            -glib \
            -no-compile-examples \
            -no-cups \
            -no-gbm \
            -no-gtk \
            -no-pch \
            -no-use-gold-linker \
            -nomake examples \
            -nomake tests \
            -opengl es2 \
            -opensource \
            -prefix /usr/local/qt5pi \
            -qpa eglfs \
            -qt-pcre \
            -reduce-exports \
            -release \
            -skip qtandroidextras \
            -skip qtcanvas3d \
            -skip qtgamepad \
            -skip qtlocation \
            -skip qtmacextras \
            -skip qtpurchasing \
            -skip qtscript \
            -skip qtwayland \
            -skip qtwinextras \
            -skip qtx11extras \
            -ssl \
            -system-freetype \
            -system-libjpeg \
            -system-libpng \
            -system-zlib \
            -sysroot /sysroot

        make -j "$(nproc --all)"
        make install
        cp -r /usr/share/fonts/truetype/dejavu/ "$SRC_DIR/qt5pi/lib/fonts"

        if [ "${BUILD_WEBENGINE-x}" == "1" ]; then
            /usr/games/cowsay -f tux "Building QTDeclarative for $1"
            fetch_qtdeclarative
            cd /src/qtdeclarative
            "$SRC_DIR/qt5pi/bin/qmake"

            make -j"$(nproc --all)"
            make install

            /usr/games/cowsay -f tux "Building QTWebEngine for $1"
            fetch_qtwebengine
            cd /src/qtwebengine
            "$SRC_DIR/qt5pi/bin/qmake"

            make -j
            make install
        fi

        cd "$SRC_DIR"
        tar zcf "$BUILD_TARGET/qtbase-$QT_BRANCH-$DEBIAN_VERSION-$1.tar.gz" qt5pi
        cd "$BUILD_TARGET"
        sha256sum "qtbase-$QT_BRANCH-$DEBIAN_VERSION-$1.tar.gz" > "qtbase-$QT_BRANCH-$DEBIAN_VERSION-$1.tar.gz.sha256"
    else
        echo "Build already exist."
    fi
}

/usr/local/bin/sysroot-relativelinks.py /sysroot

fetch_qt
fetch_cross_compile_tool
fetch_rpi_firmware

for device in pi4 pi3 pi2 pi1; do
    build_qtbase "$device"
done
