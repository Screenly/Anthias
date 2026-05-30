#!/bin/bash

# Copy balena.yml into a deploy directory and stamp it with the release
# version, so balenaCloud's Releases page shows our real CalVer version
# (e.g. 2026.5.1) instead of the default 0.0.0+revN it assigns when no
# version field is present.
#
# balenaCloud reads `version` from the balena.yml in the deploy source
# directory on `balena push`/`balena deploy`. It requires a strict
# 3-segment semver with NO leading zeros (SemVer §9). Our CalVer
# zero-pads the month (2026.05.1), which balena rejects as non-compliant
# and silently displays as 0.0.0 — so each numeric segment is normalized
# (2026.05.1 -> 2026.5.1) before stamping.

set -euo pipefail

DEST_DIR="${1:?usage: render_balena_yml.sh <dest-dir> <raw-version>}"
RAW_VERSION="${2:?usage: render_balena_yml.sh <dest-dir> <raw-version>}"
SRC_YML="${SRC_YML:-balena.yml}"

# Drop leading zeros per segment via base-10 arithmetic. The 10# prefix
# is mandatory: a bare $((05)) is parsed as octal and errors out. Empty
# segments default to 0 so a malformed input fails as 0.0.0 rather than
# crashing mid-deploy.
normalize_semver() {
    local raw="$1" major minor patch
    IFS='.' read -r major minor patch _ <<< "$raw"
    printf '%d.%d.%d\n' \
        "$((10#${major:-0}))" \
        "$((10#${minor:-0}))" \
        "$((10#${patch:-0}))"
}

VERSION="$(normalize_semver "$RAW_VERSION")"

mkdir -p "$DEST_DIR"

# Strip any existing top-level `version:` line so re-stamping is
# idempotent and a future static version in balena.yml can't produce a
# duplicate key.
grep -v '^version:' "$SRC_YML" > "$DEST_DIR/balena.yml"

# Append unquoted — single-quoted values have tripped balena-cli's
# semver parser into ignoring the field (balenaForums: "balena.yml
# version is not working at all").
printf 'version: %s\n' "$VERSION" >> "$DEST_DIR/balena.yml"

echo "Stamped balena.yml with version $VERSION (from $RAW_VERSION)"
