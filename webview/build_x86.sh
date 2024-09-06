#!/bin/bash

set -euo pipefail


# Enable script debugging if the DEBUG environment variable is set and non-zero.
if [ "${DEBUG:-0}" -ne 0 ]; then
    set -x
fi

CORE_COUNT="$(expr $(nproc) - 2)"

function install_qt() {
    QT_MAJOR='6'
    QT_MINOR='6'
    QT_PATCH='3'
    QT_VERSION="${QT_MAJOR}.${QT_MINOR}.${QT_PATCH}"
    QT_RELEASES_URL="https://download.qt.io/official_releases/qt"
    QT_DOWNLOAD_BASE_URL="${QT_RELEASES_URL}/${QT_MAJOR}.${QT_MINOR}/${QT_VERSION}/submodules"
    QT_ARCHIVE_FILES=(
        "qtbase-everywhere-src-${QT_VERSION}.tar.xz"
        "qtshadertools-everywhere-src-${QT_VERSION}.tar.xz"
        "qtdeclarative-everywhere-src-${QT_VERSION}.tar.xz"
        "qtwebengine-everywhere-src-${QT_VERSION}.tar.xz"
    )
    QT6_DIR="/build/qt6"
    QT6_SRC_PATH="${QT6_DIR}/src"
    QT6_HOST_BUILD_PATH="${QT6_DIR}/host-build"
    QT6_HOST_STAGING_PATH="${QT6_DIR}/host"

    cd /build
    mkdir -p qt6 qt6/host qt6/host-build qt6/src

    cd ${QT6_SRC_PATH}

    for QT_ARCHIVE_FILE in "${QT_ARCHIVE_FILES[@]}"; do
        if [ ! -f "${QT_ARCHIVE_FILE}" ]; then
            wget "${QT_DOWNLOAD_BASE_URL}/${QT_ARCHIVE_FILE}"
        else
            echo "File ${QT_ARCHIVE_FILE} already exists. Skipping download..."
        fi
    done

    cd ${QT6_HOST_BUILD_PATH}

    for QT_ARCHIVE_FILE in "${QT_ARCHIVE_FILES[@]}"; do
        tar xf ${QT6_SRC_PATH}/${QT_ARCHIVE_FILE}
    done

    echo "Compile Qt Base for the Host"
    cd ${QT6_HOST_BUILD_PATH}/qtbase-everywhere-src-${QT_VERSION}
    cmake -GNinja -DCMAKE_BUILD_TYPE=Release \
        -DQT_BUILD_EXAMPLES=OFF \
        -DQT_BUILD_TESTS=OFF \
        -DCMAKE_INSTALL_PREFIX=${QT6_HOST_STAGING_PATH}
    cmake --build . --parallel "${CORE_COUNT}"
    cmake --install .

    echo "Compile Qt Shader Tools for the Host"
    cd ${QT6_HOST_BUILD_PATH}/qtshadertools-everywhere-src-${QT_VERSION}
    /build/qt6/host/bin/qt-configure-module .
    cmake --build . --parallel "${CORE_COUNT}"
    cmake --install .

    echo "Compile Qt Declarative for the Host"
    cd ${QT6_HOST_BUILD_PATH}/qtdeclarative-everywhere-src-${QT_VERSION}
    /build/qt6/host/bin/qt-configure-module .
    cmake --build . --parallel "${CORE_COUNT}"
    cmake --install .

    echo "Compile Qt WebEngine for host"
    cd ${QT6_HOST_BUILD_PATH}/qtwebengine-everywhere-src-${QT_VERSION}
    /build/qt6/host/bin/qt-configure-module .
    cmake --build . --parallel "${CORE_COUNT}"
    cmake --install .

    echo "Compilation is finished"
}

function create_qt_archive() {
    cd /build
    mkdir -p release && cd release
    tar -czvf qt-host-binaries-aarch64.tar.gz -C /build/qt6/host .
}

function main() {
    install_qt
    create_qt_archive
}

main
