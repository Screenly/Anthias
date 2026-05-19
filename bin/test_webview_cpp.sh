#!/usr/bin/env bash
# Build and run AnthiasViewer's QtTest unit tests.
#
# Requires Qt 6 (qt6-base-dev, qt6-multimedia-dev). The viewer
# Docker image already ships those for the per-board builder stage
# — running this inside that container is the canonical
# environment.
#
# Usage: bin/test_webview_cpp.sh
#
# The tests run under ``QT_QPA_PLATFORM=offscreen`` so no real
# display server is needed; QtMultimedia's playback pipeline won't
# fully initialise (no rendering target) but the API surface plus
# the QGraphicsVideoItem rotation transform are testable that way.
# Decoder engagement + drop counts are exercised on real devices
# via the BBB test bed. CI integration is a follow-up.

set -euo pipefail

cd "$(dirname "$0")/.."

WEBVIEW_DIR="src/anthias_webview"
BUILD_DIR="${WEBVIEW_DIR}/tests/build"

mkdir -p "${BUILD_DIR}"
pushd "${BUILD_DIR}" >/dev/null

qmake6 ../tests.pro
make -j"$(nproc)"

# QTEST_MAIN's generated binary exits non-zero on any test failure.
# ``QT_QPA_PLATFORM=offscreen`` skips connecting to a real display
# server / framebuffer — the tests don't render, they exercise
# the QMediaPlayer / QGraphicsVideoItem API surface plus the
# rotation transform.
QT_QPA_PLATFORM=offscreen ./AnthiasViewerTests

popd >/dev/null
