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

# Strip leading zeros per segment via base-10 arithmetic (2026.05.1 ->
# 2026.5.1). Reject anything that isn't three dot-separated decimal
# integers up front: a non-numeric segment (a pre-release tag like
# 2026.05.1-rc1, or a stray branch name from a manual dispatch) would
# otherwise make the `10#` arithmetic abort with a cryptic "unbound
# variable" / "value too great for base" under `set -u`. Failing loudly
# here beats both that crash and silently stamping a bogus 0.0.0 — which
# would reintroduce the default-version bug this script exists to fix.
normalize_semver() {
    local raw="$1"
    if [[ ! "$raw" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "render_balena_yml.sh: '$raw' is not a 3-segment numeric" \
             "semver (expected MAJOR.MINOR.PATCH, e.g. 2026.5.1)" >&2
        exit 1
    fi
    local major minor patch
    IFS='.' read -r major minor patch <<< "$raw"
    # The 10# prefix is mandatory: a bare $((05)) is parsed as octal.
    printf '%d.%d.%d\n' "$((10#$major))" "$((10#$minor))" "$((10#$patch))"
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
