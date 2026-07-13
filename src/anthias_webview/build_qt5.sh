#!/bin/bash
# Qt 5 toolchain builder. Run via bin/rebuild_qt5_toolchain.sh inside
# the src/anthias_webview/Dockerfile builder image; emits
# qt5-5.15.19-trixie-{pi2,pi3}.tar.gz under /build for upload to a
# WebView-v* GitHub release. Not wired into CI — the viewer image
# now compiles the webview app inline against the toolchain artifact
# this script produces (see docker/Dockerfile.qt5-webview-builder.j2).

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -exuo pipefail

BUILD_TARGET=/build
SRC=/src
QT_MAJOR="5"
QT_MINOR="15"
QT_BUG_FIX="19"
QT_VERSION="$QT_MAJOR.$QT_MINOR.$QT_BUG_FIX"
DEBIAN_VERSION=$(lsb_release -cs)

# Debian trixie ships Python 3.13, which removed stdlib modules Chromium
# 87's build tooling still imports: `imp` (gone 3.12), `pipes`/`cgi`
# (gone 3.13). Install importlib-backed shims (src/anthias_webview/pyshim/)
# into python3's site-packages ONLY. Do NOT put them on a shared
# PYTHONPATH: the build runs some codegen actions under python2.7 (which
# has real pipes/imp/cgi), and a shared PYTHONPATH would shadow those with
# the py3-only shim and break it ("cannot import name quote"). python3's
# site-packages is only consulted after the stdlib, so it fills the gaps
# for removed modules without shadowing anything that still exists.
# These shims are required on Python 3.13, so let a failed copy abort the
# build (set -e) rather than surface later as a confusing ImportError deep
# inside a Chromium gn/ninja action.
# `ls` guard so an empty (or absent) pyshim dir doesn't feed cp a literal
# unexpanded glob and abort the build under set -e.
if ls /webview/pyshim/*.py >/dev/null 2>&1; then
    PY3_SITE="$(python3 -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
    cp -f /webview/pyshim/*.py "$PY3_SITE/"
fi

# Chromium 87's grit/build tooling `import six` (and six.moves); trixie's
# python3.13 ships no six by default, so the gn/ninja codegen actions
# abort with "No module named 'six.moves'". Install the distro package
# (six 1.17 supports 3.13). No pip fallback — the builder image has apt
# but not necessarily python3-pip, and a failed apt install should abort
# the build (set -e) rather than pretend a missing pip3 could recover it.
if ! python3 -c 'import six.moves' 2>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq python3-six
fi
# Chromium 87's licenses/gyp tooling imports distutils (spawn, version,
# dir_util, archive_util), removed from the stdlib in Python 3.12.
# setuptools re-provides it via its distutils-precedence .pth. apt-only for
# the same reason as six above.
if ! python3 -c 'import distutils.spawn' 2>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq python3-setuptools
fi

# Force -mno-unaligned-access on EVERY armhf compile. Modern Debian gcc
# lowers struct zero-init/copy of an 8-byte-aligned type into a NEON
# block-move store with a :64 alignment assertion (arm_block_set_aligned_vect);
# on the Cortex-A7 (Pi 2) that SIGBUSes whenever the object is only
# 4-byte-aligned (Chromium/Qt misaligned-pointer UB), blanking the viewer.
# GCC won't change this (PR 93031 WONTFIX). -mno-unaligned-access makes
# arm_block_set_vect bail (`... && !unaligned_access) return false`), so gcc
# emits 4-byte-safe `vstr` instead. arm_use_neon=false (common.pri, below)
# covers only Chromium's gn C++; Qt's own libs (libQt5Core/Gui/Qml/Quick,
# built by qmake with -mfpu=neon-vfpv4) and the WebEngine integration layer
# still emit it, so wrap the compiler itself — both qmake and gn resolve
# arm-linux-gnueabihf-{gcc,g++} through /usr/bin. Move the original aside
# to <tool>.real first so the wrapper never execs itself (safe whether the
# original is an alternatives symlink or a real binary). Guarded on the
# tool existing and not already being wrapped, so it's idempotent and
# doesn't fail before fetch_cross_compile_tool's own presence check.
for _t in gcc g++; do
    _bin="/usr/bin/arm-linux-gnueabihf-$_t"
    if [ -e "$_bin" ] && [ ! -e "$_bin.real" ]; then
        mv "$_bin" "$_bin.real"
        cat > "$_bin" <<EOF
#!/bin/sh
exec "$_bin.real" -mno-unaligned-access "\$@"
EOF
        chmod +x "$_bin"
        echo "Wrapped $_bin with -mno-unaligned-access"
    fi
done

# MAKE_CORES caps parallelism. Overridable via env so the wrapper can
# tune for available memory: each cc1plus under qemu-arm peaks at
# ~3-4 GB during the chromium compile, so the default `nproc + 2`
# happily OOMs anything <40 GB RAM on a 16-core box.
MAKE_CORES="${MAKE_CORES:-$(expr $(nproc) + 2)}"

# QtWebEngine's chromium build does NOT inherit `make -j`: qmake shells
# out to its bundled ninja, which self-detects nproc and floods the box
# with ~nproc parallel cc1plus regardless of MAKE_CORES. That inner
# ninja is the real RAM driver (it, not the outer make, caused the
# box-wide OOMs). NINJAFLAGS is the documented knob qtwebengine honours,
# so pin it to MAKE_CORES too — otherwise MAKE_CORES=1 is a no-op for
# the only phase that actually matters.
export NINJAFLAGS="-j${MAKE_CORES}"

mkdir -p "$BUILD_TARGET"
mkdir -p "$SRC"

/usr/games/cowsay -f tux "Building QT version $QT_VERSION."
if [ "${BUILD_WEBENGINE-x}" == "1" ]; then
    /usr/games/cowsay -f tux "...with QTWebEngine."
fi

function fetch_cross_compile_tool () {
    # The Raspberry Pi Foundation's cross compiling tools are too old, so
    # we use Debian's supported armhf cross-toolchain. (We previously
    # fetched Linaro's gcc-7.4.1, but Linaro retired releases.linaro.org.)
    # Expose it under the legacy gcc-linaro path so the CROSS_COMPILE
    # baked into qmake.conf below keeps resolving.
    if ! command -v arm-linux-gnueabihf-g++ >/dev/null 2>&1; then
        echo "error: arm-linux-gnueabihf cross toolchain not found — " \
             "install crossbuild-essential-armhf (see ./Dockerfile)." >&2
        exit 1
    fi
    # ln -sf (no -d skip) so reruns refresh the shim idempotently.
    local linaro_path="/src/gcc-linaro-7.4.1-2019.02-x86_64_arm-linux-gnueabihf"
    mkdir -p "$linaro_path/bin"
    for tool in /usr/bin/arm-linux-gnueabihf-*; do
        ln -sf "$tool" "$linaro_path/bin/$(basename "$tool")"
    done
}

function fetch_rpi_firmware () {
    # Skip the /opt/vc fetch on Debian >10. The packaged libraspberrypi0
    # from archive.raspbian.org now provides /opt/vc/lib at install time
    # (verified on trixie/armhf), so the GitHub-firmware checkout is
    # only needed on legacy bullseye/buster builders.
    _DEBIAN_VERSION=$(lsb_release -rs)
    if [ "${_DEBIAN_VERSION}" -gt "10" ]; then
        echo "Debian version is newer than 10. Skipping firmware fetch."
        return
    fi

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
    sed -i 's/lEGL/lbrcmEGL/' "/src/qt$QT_MAJOR/qtbase/mkspecs/devices/$1/qmake.conf"
    sed -i 's/lGLESv2/lbrcmGLESv2/' "/src/qt$QT_MAJOR/qtbase/mkspecs/devices/$1/qmake.conf"

    # Qmake won't account for sysroot
    # https://wiki.qt.io/RaspberryPi2EGLFS
    sed -i 's#^VC_LIBRARY_PATH.*#VC_LIBRARY_PATH = $$[QT_SYSROOT]/opt/vc/lib#' "/src/qt$QT_MAJOR/qtbase/mkspecs/devices/$1/qmake.conf"
    sed -i 's#^VC_INCLUDE_PATH.*#VC_INCLUDE_PATH = $$[QT_SYSROOT]/opt/vc/include#' "/src/qt$QT_MAJOR/qtbase/mkspecs/devices/$1/qmake.conf"
    sed -i 's#^VC_LINK_LINE.*#VC_LINK_LINE = -L$${VC_LIBRARY_PATH}#' "/src/qt$QT_MAJOR/qtbase/mkspecs/devices/$1/qmake.conf"
    sed -i 's#^QMAKE_LIBDIR_OPENGL_ES2.*#QMAKE_LIBDIR_OPENGL_ES2 = $${VC_LIBRARY_PATH}#' "/src/qt$QT_MAJOR/qtbase/mkspecs/devices/$1/qmake.conf"
}

function patch_qtwebengine () {
    # Patch up WebEngine due to GCC bug
    # https://www.enricozini.org/blog/2020/qt5/build-qt5-cross-builder-with-raspbian-sysroot-compiling-with-the-sysroot/
    pushd "/src/qt$QT_MAJOR/qtwebengine"
    sed -i '1s/^/#pragma GCC push_options\n#pragma GCC optimize ("O0")\n/' src/3rdparty/chromium/third_party/skia/third_party/skcms/skcms.cc
    echo "#pragma GCC pop_options" >> src/3rdparty/chromium/third_party/skia/third_party/skcms/skcms.cc
    popd
}

function fetch_qt () {
    local SRC_DIR="/src/qt$QT_MAJOR"
    pushd /src

    if [ ! -d "$SRC_DIR" ]; then

        if [ ! -f "qt-everywhere-opensource-src-$QT_VERSION.tar.xz" ]; then
            wget https://download.qt.io/archive/qt/$QT_MAJOR.$QT_MINOR/$QT_VERSION/single/qt-everywhere-opensource-src-$QT_VERSION.tar.xz
        fi

        if [ ! -f "md5sums.txt" ]; then
            wget https://download.qt.io/archive/qt/$QT_MAJOR.$QT_MINOR/$QT_VERSION/single/md5sums.txt
        fi
        md5sum --ignore-missing -c md5sums.txt

        # Extract and make a clone
        tar xf qt-everywhere-opensource-src-$QT_VERSION.tar.xz
        rsync -aqP qt-everywhere-src-$QT_VERSION/ qt$QT_MAJOR
    else
        rsync -aqP --delete qt-everywhere-src-$QT_VERSION/ qt$QT_MAJOR
    fi
    popd
}

function build_qt () {
    # This build process is inspired by
    # https://www.tal.org/tutorials/building-qt-512-raspberry-pi
    local SRC_DIR="/src/$1"


    if [ ! -f "$BUILD_TARGET/qt$QT_MAJOR-$QT_VERSION-$DEBIAN_VERSION-$1.tar.gz" ]; then
        /usr/games/cowsay -f tux "Building QT for $1"

        # Make sure we have a clean QT 5 tree
        fetch_qt

        # Chromium 87 vendors six 1.14, whose ``six.moves`` meta-path
        # importer is broken under Python 3.12+ (grit then dies with
        # "No module named 'six.moves'"). Overwrite every vendored
        # six.py with the distro's 3.13-compatible six so the gn/grit
        # codegen actions — which import the vendored copy ahead of any
        # site-packages — get a working ``six.moves``. Runs after
        # fetch_qt because that rsync --delete restores the stale copy.
        # grit inserts tools/grit/third_party at sys.path[0] and vendors
        # six 1.10 as a *package* (six/__init__.py) there, so overwrite
        # both the six.py files and every six/ package __init__.py with
        # the distro copy.
        # No error swallowing: six was installed above, so the import must
        # succeed, and a failed overwrite would only resurface later as the
        # "No module named 'six.moves'" grit failure this is meant to fix.
        SYS_SIX="$(python3 -c 'import six, sys; sys.stdout.write(six.__file__)')"
        CHROMIUM_DIR="/src/qt$QT_MAJOR/qtwebengine/src/3rdparty/chromium"
        find "$CHROMIUM_DIR" \
            \( -path '*/six/six.py' -o -path '*/six/__init__.py' \) \
            -print -exec cp "$SYS_SIX" {} \;

        # Force arm_use_neon=false for the Chromium build (Yocto's fix:
        # OSSystems meta-chromium / lgsvl meta-lgsvl-browser). QtWebEngine
        # leaves arm_use_neon unset, so Chromium's arm.gni default resolves
        # it to true on Linux and applies -mfpu=neon to EVERY C++ TU. Modern
        # Debian gcc (12-16) then compiles struct zero-init/copy of 8-byte-
        # aligned types into a NEON block-move store with a :64 alignment
        # assertion (arm_block_set_aligned_vect); on the Cortex-A7 (Pi 2)
        # that faults (SIGBUS) whenever the object lands 4-byte-aligned,
        # blanking the viewer. With arm_use_neon=false the general C++ path
        # compiles -mfpu=vfpv3-d16 (no NEON block store), while codec/
        # graphics (libvpx, Skia) re-add -mfpu=neon per-file with runtime
        # detection, so hardware SIMD decode is preserved. Appended after
        # fetch_qt because rsync --delete restores the pristine .pri;
        # grep-guarded so a re-run in the same workdir doesn't duplicate it.
        _common_pri="/src/qt$QT_MAJOR/qtwebengine/src/core/config/common.pri"
        grep -qF 'arm_use_neon=false' "$_common_pri" \
            || echo 'gn_args += arm_use_neon=false' >> "$_common_pri"

        if [ "${CLEAN_BUILD-x}" == "1" ]; then
            rm -rf "$SRC_DIR"
        fi

        mkdir -p "$SRC_DIR"
        pushd "$SRC_DIR"

        if [ "$1" = "pi2" ]; then
            local BUILD_ARGS=(
                "-device" "linux-rasp-pi2-g++"
            )
            patch_qt "linux-rasp-pi2-g++"
        elif [ "$1" = "pi3" ]; then
            local BUILD_ARGS=(
                "-device" "linux-rasp-pi3-g++"
            )
            patch_qt "linux-rasp-pi3-g++"
        else
            echo "Unknown device. Exiting."
            exit 1
        fi

        # @TODO: Add in the `-opengl es2` flag for Pi 1 - Pi 3.
        # Currently this breaks the QTWebEngine process.
        /src/qt$QT_MAJOR/configure \
            "${BUILD_ARGS[@]}" \
            -ccache \
            -confirm-license \
            -dbus-linked \
            -device-option CROSS_COMPILE=/src/gcc-linaro-7.4.1-2019.02-x86_64_arm-linux-gnueabihf/bin/arm-linux-gnueabihf- \
            -eglfs \
            -evdev \
            -extprefix "$SRC_DIR/qt${QT_MAJOR}pi" \
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
            -prefix "/usr/local/qt${QT_MAJOR}pi" \
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
            -sysroot /sysroot \
            -webengine-proprietary-codecs

        # The RAM consumption is proportional to the amount of cores.
        # On an 8 core box, the build process will require ~16GB of RAM.
        make -j"$MAKE_CORES"
        make install

        # I'm not sure we actually need this anymore. It's from an
        # old build process for QT 4.9 that we used.
        cp -r /usr/share/fonts/truetype/dejavu/ "$SRC_DIR/qt${QT_MAJOR}pi/lib/fonts"

        pushd "$SRC_DIR"
        tar cfz "$BUILD_TARGET/qt$QT_MAJOR-$QT_VERSION-$DEBIAN_VERSION-$1.tar.gz" qt${QT_MAJOR}pi
        popd

        pushd "$BUILD_TARGET"
        sha256sum "qt$QT_MAJOR-$QT_VERSION-$DEBIAN_VERSION-$1.tar.gz" > "qt$QT_MAJOR-$QT_VERSION-$DEBIAN_VERSION-$1.tar.gz.sha256"
        popd
    else
        echo "QT Build already exist."
    fi

    # The webview app itself is now compiled inside the viewer image
    # (docker/Dockerfile.qt5-webview-builder.j2 includes this Qt 5
    # toolchain at build time). This script only emits the toolchain
    # tarball — bin/rebuild_qt5_toolchain.sh uploads it to the frozen
    # WebView-v2026.07.1 release.
}

# Modify paths for build process
python3 /usr/local/bin/sysroot-relativelinks.py /sysroot

fetch_cross_compile_tool
fetch_rpi_firmware

if [ ! "${TARGET-}" ]; then
    # Iterate the surviving Qt 5 boards. Pi 1 and the 32-bit Pi 4 path
    # were retired with the Trixie / drop-Balena upgrade; Pi 4 64-bit
    # and Pi 5 use Qt 6 from Debian apt (built inline in the viewer
    # image's webview-builder stage; see docker/Dockerfile.viewer.j2).
    for device in pi3 pi2; do
        build_qt "$device"
    done
else
    build_qt "$TARGET"
fi

