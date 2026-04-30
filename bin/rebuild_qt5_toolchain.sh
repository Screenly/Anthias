#!/usr/bin/env bash
#
# Rebuild the Qt 5 cross-compile toolchain tarballs for the surviving
# 32-bit Pi boards (pi2, pi3) on Debian Trixie. Produces
#     qt5-5.15.14-trixie-{pi2,pi3}.tar.gz       (+ .sha256)
# under .qt5-toolchain-build/release/ at the repo root, ready to be
# attached to a `WebView-v*` GitHub release.
#
# This is an out-of-band prereq for the Trixie migration:
# webview/build_webview_with_qt5.sh fetches these tarballs from the
# release tag pinned at line 21 (QT5_TOOLCHAIN_TAG). If that release
# doesn't yet have trixie-* artifacts, pi2/pi3 CI fails with
#     sha256sum: no properly formatted checksum lines found
# (the curl on the missing .sha256 falls back to a 404 HTML page).
#
# Runtime: ~2-4 hours per board on a beefy x86 host (Qt 5 + QtWebEngine
# under qemu-arm). The build is RAM-hungry: build_qt5.sh runs make with
# nproc+2 jobs and Qt 5 + WebEngine peaks at roughly 2 GB per job, so an
# 8-core box wants ≥ 16 GB free. Boards are built sequentially because
# (a) two parallel builds will OOM most hosts and (b) the Linaro
# cross-compile toolchain extracted under .qt5-toolchain-build/src is
# shared and isn't safe to extract concurrently.
#
# Usage:
#   bin/rebuild_qt5_toolchain.sh                # both pi2 + pi3
#   bin/rebuild_qt5_toolchain.sh pi2            # one board
#   bin/rebuild_qt5_toolchain.sh pi2 pi3        # both, explicit
#
# Idempotent: rerunning skips a board whose tarball already exists.
# Delete the .tar.gz under .qt5-toolchain-build/release/ to force a
# rebuild for that board. Set BUILD_WEBVIEW=0 to skip the bonus
# webview-* tarball build (the Dockerfile defaults BUILD_WEBVIEW=1, so
# build_qt5.sh produces both qt5-* and webview-* by default; passing
# BUILD_WEBVIEW=0 here overrides that for a toolchain-only run).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEBVIEW_DIR="${REPO_ROOT}/webview"
BUILD_ROOT="${REPO_ROOT}/.qt5-toolchain-build"
SRC_DIR="${BUILD_ROOT}/src"
OUT_DIR="${BUILD_ROOT}/release"
CCACHE_DIR="${BUILD_ROOT}/ccache"
IMAGE_TAG="webview-qt5-builder:latest"

DEFAULT_BOARDS=(pi2 pi3)
BOARDS=("$@")
if [[ ${#BOARDS[@]} -eq 0 ]]; then
    BOARDS=("${DEFAULT_BOARDS[@]}")
fi

for board in "${BOARDS[@]}"; do
    if [[ "${board}" != "pi2" && "${board}" != "pi3" ]]; then
        echo "error: unsupported board '${board}' (expected pi2 or pi3)" >&2
        exit 1
    fi
done

if ! command -v docker >/dev/null 2>&1; then
    echo "error: docker is required but not found in PATH" >&2
    exit 1
fi

mkdir -p "${SRC_DIR}" "${OUT_DIR}" "${CCACHE_DIR}"

echo "==> Building ${IMAGE_TAG} (BuildKit layer cache will short-circuit if unchanged)"
GIT_SHORT_HASH=$(git -C "${REPO_ROOT}" rev-parse --short HEAD)
GIT_BRANCH=$(git -C "${REPO_ROOT}" rev-parse --abbrev-ref HEAD)
docker buildx build \
    --load \
    --build-arg "BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
    --build-arg "GIT_HASH=${GIT_SHORT_HASH}" \
    --build-arg "GIT_SHORT_HASH=${GIT_SHORT_HASH}" \
    --build-arg "GIT_BRANCH=${GIT_BRANCH}" \
    -t "${IMAGE_TAG}" \
    "${WEBVIEW_DIR}"

WEBVIEW_VERSION="${WEBVIEW_VERSION:-$(date -u +%Y.%m).0-dev}"

for board in "${BOARDS[@]}"; do
    expected_tarball="${OUT_DIR}/qt5-5.15.14-trixie-${board}.tar.gz"
    if [[ -f "${expected_tarball}" ]]; then
        echo "==> ${expected_tarball} already exists; skipping ${board}"
        echo "    (delete the .tar.gz to force a rebuild)"
        continue
    fi

    echo "==> Building Qt 5 toolchain for ${board} (this will take hours)"
    docker run --rm \
        --name "qt5-toolchain-${board}" \
        -v "${SRC_DIR}:/src:Z" \
        -v "${CCACHE_DIR}:/src/ccache:Z" \
        -v "${OUT_DIR}:/build:Z" \
        -v "${WEBVIEW_DIR}:/webview:ro" \
        -e "TARGET=${board}" \
        -e "WEBVIEW_VERSION=${WEBVIEW_VERSION}" \
        -e "BUILD_WEBVIEW=${BUILD_WEBVIEW:-1}" \
        "${IMAGE_TAG}" \
        /webview/build_qt5.sh
done

echo
echo "==> Toolchain build complete. Artifacts in ${OUT_DIR}:"
ls -lah "${OUT_DIR}" 2>/dev/null || true
echo
echo "Verify checksums:"
echo "  (cd '${OUT_DIR}' && sha256sum -c qt5-5.15.14-trixie-*.tar.gz.sha256)"
echo
echo "Upload to a WebView-v* release. If you re-use the tag pinned at"
echo "webview/build_webview_with_qt5.sh:21 (currently WebView-v0.3.5),"
echo "no source change is needed; otherwise bump QT5_TOOLCHAIN_TAG to"
echo "the new tag in the same commit."
echo "  gh release upload <WebView-vX.Y.Z> \\"
echo "      '${OUT_DIR}'/qt5-5.15.14-trixie-pi2.tar.gz{,.sha256} \\"
echo "      '${OUT_DIR}'/qt5-5.15.14-trixie-pi3.tar.gz{,.sha256}"
