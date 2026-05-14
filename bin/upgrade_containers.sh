#!/bin/bash -e

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

# Rename legacy ~/screenly, ~/.screenly, ~/screenly_assets paths to
# their anthias equivalents. The helper self-relocates and re-execs
# from /tmp if it lives inside the dir being renamed, so this also
# handles the case where the running script's path was ~/screenly/...
# Idempotent / no-op on fresh installs and post-migration runs.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"${SCRIPT_DIR}/migrate_legacy_paths.sh"

# Export various environment variables
TOTAL_MEMORY_KB=$(grep MemTotal /proc/meminfo | awk {'print $2'})
export VIEWER_MEMORY_LIMIT_KB=$(echo "$TOTAL_MEMORY_KB" \* 0.8 | bc)
export SHM_SIZE_KB="$(echo "$TOTAL_MEMORY_KB" \* 0.3 | bc | cut -d'.' -f1)"
# Hard cgroup CPU cap for anthias-celery. Half the host's cores
# (floored to 1.0 so single-core boxes still make progress) keeps
# the upload-time normalisation pipeline from starving the viewer
# or sshd even when libx265 wants every cycle it can get. On a
# Pi 4 / Pi 5 / Rock Pi 4 (4 cores) that's 2 CPUs' worth of
# compute, leaving 2 for the viewer + system. On an 8-core x86
# box that's 4 CPUs, leaving 4 for everything else. Live-
# confirmed on the Rock Pi 4 that ``nice -n 19`` + ``ionice -c 3``
# alone are insufficient — the kernel still hands libx265 every
# available cycle if nothing else is asking for them, which
# starves sshd through banner exchange and drops mpv frames.
CELERY_CPU_LIMIT_RAW=$(echo "$(nproc) * 0.5" | bc -l)
export CELERY_CPU_LIMIT=$(awk -v v="$CELERY_CPU_LIMIT_RAW" 'BEGIN { printf "%.1f", (v < 1.0 ? 1.0 : v) }')
GIT_BRANCH="${GIT_BRANCH:-master}"

MODE="${MODE:-pull}"
if [[ ! "$MODE" =~ ^(pull|build)$ ]]; then
    echo "Invalid mode: $MODE"
    echo "Usage: MODE=(pull|build) $0"
    exit 1
fi

# Host MAC of the interface carrying the default route — used as the
# device identifier (exposed via /api/v2/ and the admin UI). The
# container only sees its own veth on the docker bridge, so we resolve
# this on the host and inject it via the MAC_ADDRESS env var below.
# Empty when no default route is published (e.g. install behind a
# captive portal); the in-container fallback in
# anthias_common/utils.py:_detect_local_mac then picks whatever the
# container can see.
DEFAULT_IFACE=$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}')
if [ -n "$DEFAULT_IFACE" ]; then
    export MAC_ADDRESS=$(cat "/sys/class/net/${DEFAULT_IFACE}/address" 2>/dev/null || echo '')
else
    export MAC_ADDRESS=''
fi

if [ -z "$DOCKER_TAG" ]; then
    export DOCKER_TAG="latest"
fi

# Detect Raspberry Pi version. Pi 4 is always treated as pi4-64 (the
# 32-bit pi4 image stream was retired with the Trixie upgrade); legacy
# 0.19.5-and-older 32-bit pi4 deployments stay on whatever DOCKER_TAG
# they were already running and don't reach this code path.
if [ ! -f /proc/device-tree/model ] && [ "$(uname -m)" = "x86_64" ]; then
    export DEVICE_TYPE="x86"
elif grep -qF "Raspberry Pi 5" /proc/device-tree/model || grep -qF "Compute Module 5" /proc/device-tree/model; then
    export DEVICE_TYPE="pi5"
elif grep -qF "Raspberry Pi 4" /proc/device-tree/model || grep -qF "Compute Module 4" /proc/device-tree/model; then
    export DEVICE_TYPE="pi4-64"
elif grep -qF "Raspberry Pi 3" /proc/device-tree/model || grep -qF "Compute Module 3" /proc/device-tree/model; then
    export DEVICE_TYPE="pi3"
elif grep -qF "Raspberry Pi 2" /proc/device-tree/model; then
    export DEVICE_TYPE="pi2"
elif [ "$(uname -m)" = "aarch64" ]; then
    # Generic 64-bit ARM SBC fallback — matches the install.sh branch.
    # Intentional catch-all: a future Pi model whose model string
    # doesn't yet match the regexes above also lands here. See
    # bin/install.sh::set_device_type for the rationale.
    export DEVICE_TYPE="arm64"
else
    echo "Unsupported device. Anthias supports Pi 2/3/4/5, x86, and 64-bit ARM SBCs." >&2
    exit 1
fi

if [[ -n $(docker ps | grep srly-ose) ]]; then
    # @TODO: Rename later
    set +e
    docker container rename srly-ose-server anthias-server
    docker container rename srly-ose-viewer anthias-viewer
    set -e
fi

# Drop legacy containers no longer in the compose file:
#   * nginx / websocket — folded into anthias-server (uvicorn).
#   * wifi-connect      — service removed; nmcli/nmtui is the supported
#                          path now.
#   * anthias-celery / srly-ose-celery containers from the era when
#     celery had its own image. The new compose file recreates the
#     anthias-celery container against ghcr.io/screenly/anthias-server,
#     so the old container (still pointing at the deleted celery image)
#     must be removed first or the server-image-backed replacement
#     can't take its name.
# Volumes are shared across services, so removing the containers is safe.
set +e
docker rm -f \
    anthias-nginx anthias-websocket anthias-wifi-connect \
    srly-ose-nginx srly-ose-websocket srly-ose-wifi-connect \
    anthias-celery srly-ose-celery \
    >/dev/null 2>&1
set -e

# Pull the host's configured locale into our shell env so envsubst can
# substitute LANG/LANGUAGE into the viewer service block (issue #480 —
# AnthiasWebview reads QLocale::system() to set Accept-Language). The
# `locales` package writes LANG=... into /etc/default/locale when the
# operator runs `raspi-config` or `update-locale`; sourcing it here is
# how those settings reach the viewer container. No-op if the file is
# missing — the compose substitutions then resolve to empty strings,
# and the webview falls back to QtWebEngine's built-in default.
if [ -f /etc/default/locale ]; then
    set -a
    . /etc/default/locale
    set +a
fi

cat /home/${USER}/anthias/docker-compose.yml.tmpl \
    | envsubst \
    > /home/${USER}/anthias/docker-compose.yml

if [[ "$DEVICE_TYPE" =~ ^(x86|pi5|arm64)$ ]]; then
    sed -i '/devices:/ {N; /\n.*\/dev\/vchiq:\/dev\/vchiq/d}' \
        /home/${USER}/anthias/docker-compose.yml
fi

COMPOSE_FILES=(-f /home/${USER}/anthias/docker-compose.yml)
SSL_OVERRIDE=/home/${USER}/anthias/docker-compose.ssl.override.yml
if [[ -f "$SSL_OVERRIDE" ]]; then
    COMPOSE_FILES+=(-f "$SSL_OVERRIDE")
fi

sudo -E docker compose "${COMPOSE_FILES[@]}" ${MODE}

if [ -f /var/run/reboot-required ]; then
    exit 0
fi

# --remove-orphans sweeps containers that linger after a service is
# renamed or removed from the compose file (e.g. legacy run-NNN sidecar
# instances left over from earlier `docker compose run` invocations).
# Without it `up -d` only logs a warning and leaves the orphans running,
# which is confusing on a `docker ps` audit later.
sudo -E docker compose "${COMPOSE_FILES[@]}" up -d --remove-orphans
