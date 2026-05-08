#!/usr/bin/env bash
# Install Bun deterministically for CI: download the pinned-version
# release archive from oven-sh/bun's GitHub release and verify its
# SHA-256 against the SHASUMS256.txt file from the same release before
# installing. Replaces `curl https://bun.sh/install | bash`, which
# executes a remote shell script at workflow runtime, and the
# third-party `oven-sh/setup-bun` action.
#
# Required env: BUN_VERSION (e.g. "1.3.13"). The GitHub-hosted runners
# we use are linux/amd64; if that ever changes, extend the platform
# detection below.
set -euo pipefail

: "${BUN_VERSION:?BUN_VERSION must be set in the environment}"

case "$(uname -m)" in
    x86_64|amd64) target=linux-x64 ;;
    aarch64|arm64) target=linux-aarch64 ;;
    *) echo "::error::unsupported runner arch $(uname -m)" >&2; exit 1 ;;
esac

release_url="https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}"
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

archive="$workdir/bun-${target}.zip"
shasums="$workdir/SHASUMS256.txt"

# --proto/--tlsv1.2 hardens curl against accidental fall-through to
# plaintext or downgraded TLS; --fail makes it exit non-zero on 4xx/5xx
# rather than writing an HTML error page to disk.
curl --fail --silent --show-error --location \
    --proto '=https' --tlsv1.2 \
    "${release_url}/bun-${target}.zip" -o "$archive"
curl --fail --silent --show-error --location \
    --proto '=https' --tlsv1.2 \
    "${release_url}/SHASUMS256.txt" -o "$shasums"

# SHASUMS256.txt and the binary come from the same github.com release
# URL, so this verification defends against in-flight corruption /
# truncation rather than a fully compromised github.com release. That
# matches the trust model of `curl bun.sh/install | bash` while
# eliminating the remote-script-execution surface.
expected=$(awk -v f="bun-${target}.zip" '$2 == f {print $1}' "$shasums")
if [[ -z "$expected" ]]; then
    echo "::error::bun-${target}.zip not listed in SHASUMS256.txt for ${BUN_VERSION}" >&2
    exit 1
fi
actual=$(sha256sum "$archive" | awk '{print $1}')
if [[ "$expected" != "$actual" ]]; then
    echo "::error::bun checksum mismatch for ${BUN_VERSION}: expected $expected, got $actual" >&2
    exit 1
fi

unzip -q "$archive" -d "$workdir"
sudo install -m 0755 "$workdir/bun-${target}/bun" /usr/local/bin/bun

bun --version
