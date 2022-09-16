#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -exuo pipefail

BUILD_TARGET=/build
SRC=/src
QT_BRANCH="5.15.2"
DEBIAN_VERSION=$(lsb_release -cs)
MAKE_CORES="$(expr $(nproc) + 2)"

mkdir -p "$BUILD_TARGET"
mkdir -p "$SRC"

/usr/games/cowsay -f tux "Building QT version $QT_BRANCH."
if [ "${BUILD_WEBENGINE-x}" == "1" ]; then
    /usr/games/cowsay -f tux "...with QTWebEngine."
fi

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

function fetch_rpi_firmware () {
    if [ ! -d "/src/opt" ]; then
        pushd /src

        # We do an `svn checkout` here as the entire git repo here is *huge*
        # and `git` doesn't  support partial checkouts well (yet)
        svn checkout -q https://github.com/raspberrypi/firmware/trunk/opt
        popd
    fi

    # We need to exclude all of these .h and android files to make QT build.
    # In the blog post referenced, this is done using `dpkg --purge libraspberrypi-dev`,
    # but since we're copying in the source, we're just going to exclude these from the rsync.
    # https://www.enricozini.org/blog/2020/qt5/build-qt5-cross-builder-with-raspbian-sysroot-compiling-with-the-sysroot-continued/
    rsync \
        -aP \
        --exclude '*android*' \
        --exclude 'hello_pi' \
        --exclude '.svn' \
        /src/opt/ /sysroot/opt/
}

function patch_qt () {
    # Yes, yes, this all should be converted to proper patches
    # but I really just wanted to get it to work.

    # QT is linking against the old libraries for Pi 1 - Pi 3
    # https://bugreports.qt.io/browse/QTBUG-62216
    sed -i 's/lEGL/lbrcmEGL/' "/src/qt5/qtbase/mkspecs/devices/$1/qmake.conf"
    sed -i 's/lGLESv2/lbrcmGLESv2/' "/src/qt5/qtbase/mkspecs/devices/$1/qmake.conf"

    # Qmake won't account for sysroot
    # https://wiki.qt.io/RaspberryPi2EGLFS
    sed -i 's#^VC_LIBRARY_PATH.*#VC_LIBRARY_PATH = $$[QT_SYSROOT]/opt/vc/lib#' "/src/qt5/qtbase/mkspecs/devices/$1/qmake.conf"
    sed -i 's#^VC_INCLUDE_PATH.*#VC_INCLUDE_PATH = $$[QT_SYSROOT]/opt/vc/include#' "/src/qt5/qtbase/mkspecs/devices/$1/qmake.conf"
    sed -i 's#^VC_LINK_LINE.*#VC_LINK_LINE = -L$${VC_LIBRARY_PATH}#' "/src/qt5/qtbase/mkspecs/devices/$1/qmake.conf"
    sed -i 's#^QMAKE_LIBDIR_OPENGL_ES2.*#QMAKE_LIBDIR_OPENGL_ES2 = $${VC_LIBRARY_PATH}#' "/src/qt5/qtbase/mkspecs/devices/$1/qmake.conf"
}

function patch_qtwebengine () {
    # Patch up WebEngine due to GCC bug
    # https://www.enricozini.org/blog/2020/qt5/build-qt5-cross-builder-with-raspbian-sysroot-compiling-with-the-sysroot/
    pushd "/src/qt5/qtwebengine"
    sed -i '1s/^/#pragma GCC push_options\n#pragma GCC optimize ("O0")\n/' src/3rdparty/chromium/third_party/skia/third_party/skcms/skcms.cc
    echo "#pragma GCC pop_options" >> src/3rdparty/chromium/third_party/skia/third_party/skcms/skcms.cc
    popd
}

function fetch_qt5 () {
    local SRC_DIR="/src/qt5"
    pushd /src

    if [ ! -d "$SRC_DIR" ]; then

        if [ ! -f "qt-everywhere-src-5.15.2.tar.xz" ]; then
            wget https://download.qt.io/archive/qt/5.15/5.15.2/single/qt-everywhere-src-5.15.2.tar.xz
        fi

        if [ ! -f "md5sums.txt" ]; then
            wget https://download.qt.io/archive/qt/5.15/5.15.2/single/md5sums.txt
        fi
        md5sum --ignore-missing -c md5sums.txt

        # Extract and make a clone
        tar xf qt-everywhere-src-5.15.2.tar.xz
        rsync -aqP qt-everywhere-src-5.15.2/ qt5
    else
        rsync -aqP --delete qt-everywhere-src-5.15.2/ qt5
    fi
    popd
}

function build_qt () {
    # This build process is inspired by
    # https://www.tal.org/tutorials/building-qt-512-raspberry-pi
    local SRC_DIR="/src/$1"


    if [ ! -f "$BUILD_TARGET/qt5-$QT_BRANCH-$DEBIAN_VERSION-$1.tar.gz" ]; then
        /usr/games/cowsay -f tux "Building QT for $1"

        # Make sure we have a clean QT 5 tree
        fetch_qt5

        if [ "${CLEAN_BUILD-x}" == "1" ]; then
            rm -rf "$SRC_DIR"
        fi

        mkdir -p "$SRC_DIR"
        pushd "$SRC_DIR"

        if [ "$1" = "pi1" ]; then
            local BUILD_ARGS=(
                "-device" "linux-rasp-pi-g++"
            )
            patch_qt "linux-rasp-pi-g++"
            patch_qtwebengine
        elif [ "$1" = "pi2" ]; then
            local BUILD_ARGS=(
                "-device" "linux-rasp-pi2-g++"
            )
            patch_qt "linux-rasp-pi2-g++"
        elif [ "$1" = "pi3" ]; then
            local BUILD_ARGS=(
                "-device" "linux-rasp-pi3-g++"
            )
            patch_qt "linux-rasp-pi3-g++"

        # The opengl flag only works on Pi 4. It breaks the QTWebEngine build
        # process on any other model.
        elif [ "$1" = "pi4" ]; then
            local BUILD_ARGS=(
                "-device" "linux-rasp-pi4-v3d-g++"
                "-opengl" "es2"
            )
        else
            echo "Unknown device. Exiting."
            exit 1
        fi

        # @TODO: Add in the `-opengl es2` flag for Pi 1 - Pi 3.
        # Currently this breaks the QTWebEngine process.
        /src/qt5/configure \
            "${BUILD_ARGS[@]}" \
            -ccache \
            -confirm-license \
            -dbus-linked \
            -device-option CROSS_COMPILE=/src/gcc-linaro-7.4.1-2019.02-x86_64_arm-linux-gnueabihf/bin/arm-linux-gnueabihf- \
            -eglfs \
            -evdev \
            -extprefix "$SRC_DIR/qt5pi" \
            -force-pkg-config \
            -glib \
            -make libs \
            -no-compile-examples \
            -no-cups \
            -no-gbm \
            -no-gtk \
            -no-pch \
            -no-use-gold-linker \
            -no-xcb \
            -no-xcb-xlib \
            -nomake examples \
            -nomake tests \
            -opensource \
            -prefix /usr/local/qt5pi \
            -qpa eglfs \
            -qt-pcre \
            -reduce-exports \
            -release \
            -skip qt3d \
            -skip qtactiveqt \
            -skip qtandroidextras \
            -skip qtcanvas3d \
            -skip qtcharts \
            -skip qtdatavis3d \
            -skip qtgamepad \
            -skip qtgraphicaleffects \
            -skip qtlocation \
            -skip qtlottie \
            -skip qtmacextras \
            -skip qtpurchasing \
            -skip qtquick3d \
            -skip qtquickcontrols \
            -skip qtquickcontrols2 \
            -skip qtquicktimeline \
            -skip qtscript \
            -skip qtscxml \
            -skip qtsensors \
            -skip qtserialbus \
            -skip qtserialport \
            -skip qtspeech \
            -skip qttools \
            -skip qttranslations \
            -skip qtvirtualkeyboard \
            -skip qtwayland \
            -skip qtwebview \
            -skip qtwinextras \
            -skip qtx11extras \
            -skip wayland \
            -ssl \
            -system-freetype \
            -system-libjpeg \
            -system-libpng \
            -system-zlib \
            -sysroot /sysroot

        # The RAM consumption is proportional to the amount of cores.
        # On an 8 core box, the build process will require ~16GB of RAM.
        make -j"$MAKE_CORES"
        make install

        # I'm not sure we actually need this anymore. It's from an
        # old build process for QT 4.9 that we used.
        cp -r /usr/share/fonts/truetype/dejavu/ "$SRC_DIR/qt5pi/lib/fonts"

        pushd "$SRC_DIR"
        tar cfz "$BUILD_TARGET/qt5-$QT_BRANCH-$DEBIAN_VERSION-$1.tar.gz" qt5pi
        popd

        pushd "$BUILD_TARGET"
        sha256sum "qt5-$QT_BRANCH-$DEBIAN_VERSION-$1.tar.gz" > "qt5-$QT_BRANCH-$DEBIAN_VERSION-$1.tar.gz.sha256"
        popd
    else
        echo "QT Build already exist."
    fi

    if [ ! -f "$BUILD_TARGET/webview-$QT_BRANCH-$DEBIAN_VERSION-$1-$GIT_HASH.tar.gz" ]; then
        if [ "${BUILD_WEBVIEW-x}" == "1" ]; then
            cp -rf /webview "$SRC_DIR/"

            pushd "$SRC_DIR/webview"

            "$SRC_DIR/qt5pi/bin/qmake"
            make -j"$MAKE_CORES"
            make install

            mkdir -p fakeroot/bin fakeroot/share/ScreenlyWebview
            mv ScreenlyWebview fakeroot/bin/
            cp -rf /webview/res fakeroot/share/ScreenlyWebview/

            pushd fakeroot
            tar cfz "$BUILD_TARGET/webview-$QT_BRANCH-$DEBIAN_VERSION-$1-$GIT_HASH.tar.gz" .
            popd

            pushd "$BUILD_TARGET"
            sha256sum "webview-$QT_BRANCH-$DEBIAN_VERSION-$1-$GIT_HASH.tar.gz" > "webview-$QT_BRANCH-$DEBIAN_VERSION-$1-$GIT_HASH.tar.gz.sha256"
            popd
        fi
    else
        echo "Webview Build already exist."
    fi
}

# Modify paths for build process
/usr/local/bin/sysroot-relativelinks.py /sysroot

fetch_cross_compile_tool
fetch_rpi_firmware

if [ ! "${TARGET-}" ]; then
    # Let's work our way through all Pis in order of relevance
    for device in pi4 pi3 pi2 pi1; do
        build_qt "$device"
    done
else
    build_qt "$TARGET"
fi

