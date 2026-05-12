#!/usr/bin/env bash
# Regenerate every favicon / apple-touch-icon / mstile asset from the
# canonical Anthias mark in website/assets/images/logo.svg. Each PNG
# is rendered with rsvg-convert at the icon's natural aspect ratio,
# then centered onto a transparent square canvas via ImageMagick so
# the asymmetric source viewBox (50x48) doesn't get stretched.
#
# Requires: rsvg-convert (librsvg2-bin), convert (imagemagick),
#           icotool (icoutils).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${REPO_ROOT}/website/assets/images/logo.svg"
DST="${REPO_ROOT}/src/anthias_server/app/static/favicons"

[ -f "${SRC}" ] || { echo "missing source SVG: ${SRC}" >&2; exit 1; }
mkdir -p "${DST}"

# square <w> <h> <out>: render SRC at natural aspect, center on
# transparent w*h canvas. For w==h this produces a square icon.
square() {
    local w="$1" h="$2" out="$3"
    local tmp; tmp="$(mktemp --suffix=.png)"
    # Render at the smaller of w/h so the icon fits without cropping.
    local fit=$(( w < h ? w : h ))
    rsvg-convert -h "${fit}" "${SRC}" -o "${tmp}"
    convert -size "${w}x${h}" xc:none "${tmp}" -gravity center -composite \
        "${out}"
    rm -f "${tmp}"
}

# Standard favicons
for s in 16 32 96 196; do square "$s" "$s" "${DST}/favicon-${s}x${s}.png"; done
square 128 128 "${DST}/favicon-128.png"

# Apple touch icons
for s in 57 60 72 76 114 120 144 152; do
    square "$s" "$s" "${DST}/apple-touch-icon-${s}x${s}.png"
done

# Microsoft tile icons (310x150 is intentionally rectangular)
for s in 70 144 150 310; do square "$s" "$s" "${DST}/mstile-${s}x${s}.png"; done
square 310 150 "${DST}/mstile-310x150.png"

# Multi-size favicon.ico (16, 32, 48 — Windows/legacy bookmark bars).
TMP_ICO_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_ICO_DIR}"' EXIT
for s in 16 32 48; do
    square "$s" "$s" "${TMP_ICO_DIR}/${s}.png"
done
icotool -c -o "${DST}/favicon.ico" \
    "${TMP_ICO_DIR}/16.png" "${TMP_ICO_DIR}/32.png" "${TMP_ICO_DIR}/48.png"

echo "regenerated favicons in ${DST}"
