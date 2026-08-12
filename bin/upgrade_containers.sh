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
# Memory cap for anthias-celery. 60% of host RAM is conservative
# headroom for the remaining celery workloads (ffprobe metadata,
# HEIC → WebP image conversion); the cap is here as a safety net
# against a decompression-bomb fixture or runaway ffprobe, not
# because routine workloads come anywhere near it.
export CELERY_MEMORY_LIMIT_KB=$(echo "$TOTAL_MEMORY_KB * 0.6" | bc | cut -d'.' -f1)
# NB: the AnthiasViewer used to be gated into single-WebEngineView mode
# on < 1.5 GiB boards via an ``ANTHIAS_LOW_RAM`` export here. That flag
# is gone — the viewer now runs a single QWebEngineView on every board
# (issue #2954: the preloaded second buffer flashed a stale foreign
# page on every webpage transition, and the fix was to drop it), which
# also reclaims the ~100 MB the second renderer cost. The independent
# 1080p upload cap on low-RAM boards is unaffected: it lives in
# anthias_server (``is_low_ram_device`` reads ``host:total_mem_kb`` from
# Redis), not this export.
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
    # 64-bit OS on a Pi 3 → arm64 Qt 6 viewer (`pi3-64`); a 32-bit OS
    # keeps the legacy armhf/Qt5 `pi3` image. See
    # bin/install.sh::set_device_type for the full rationale.
    #
    # Key off the *userspace* arch, not `uname -m`: 32-bit Raspberry Pi
    # OS ships a 64-bit kernel by default on Pi 3 (arm_64bit=1), so
    # `uname -m` reports aarch64 even though Docker/apt are armhf. Pulling
    # the arm64 `pi3-64` image there fails with "no matching manifest for
    # linux/arm/v8". `dpkg --print-architecture` maps 1:1 to the image
    # platform (armhf/arm64).
    if [ "$(dpkg --print-architecture)" = "arm64" ]; then
        export DEVICE_TYPE="pi3-64"
    else
        export DEVICE_TYPE="pi3"
    fi
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
#   * srly-ose-redis — pre-rebrand Redis container. Still bound to
#     127.0.0.1:6379, so the new anthias-redis can't claim the port
#     until it's gone (forum.screenly.io/t/6688).
# Volumes are shared across services, so removing the containers is safe.
set +e
docker rm -f \
    anthias-nginx anthias-websocket anthias-wifi-connect \
    srly-ose-nginx srly-ose-websocket srly-ose-wifi-connect \
    anthias-celery srly-ose-celery \
    srly-ose-redis \
    >/dev/null 2>&1
set -e

# Pull the host's configured locale into our shell env so envsubst can
# substitute LANG/LANGUAGE into the viewer service block (issue #480 —
# AnthiasViewer reads QLocale::system() to set Accept-Language). The
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

# Pull the configured HTTP proxy into our shell env so envsubst renders
# HTTP_PROXY/HTTPS_PROXY/NO_PROXY into the server/viewer/celery service
# blocks (GH #3239). /etc/anthias/proxy.env is the single source of truth
# written by the ansible system role; when it is absent (no proxy) the
# substitutions resolve to empty strings, which every client treats as
# "no proxy". `set -a` exports them so the envsubst child process sees them.
if [[ -f /etc/anthias/proxy.env ]]; then
    set -a
    . /etc/anthias/proxy.env
    set +a
fi

cat /home/${USER}/anthias/docker-compose.yml.tmpl \
    | envsubst \
    > /home/${USER}/anthias/docker-compose.yml

# CEC device passthrough.
#
# `docker compose` refuses to start a container whose listed host node
# is missing, so the device list has to match the host exactly. It used
# to be derived from $DEVICE_TYPE, which was wrong in both directions:
# pi2/pi3/pi3-64/pi4-64 fell through the case entirely and kept
# /dev/vchiq (a node libcec cannot use on a mainline-KMS kernel, and
# which the current implementation does not use at all), while pi5 got
# a hardcoded /dev/cec0 + /dev/cec1 (GH #3267).
#
# Which adapters exist is a property of the host, not of the board
# name, so enumerate them instead of guessing. Measured across the
# testbed fleet: the nodes are created by the SoC's HDMI driver and are
# present whether or not anything is plugged in (a Pi 3 A+ with a
# disconnected HDMI still has /dev/cec0), and a board with two HDMI
# outputs has one node per output (Pi 4 and Pi 5 both expose
# /dev/cec0 + /dev/cec1). x86 has none at all unless an add-on adapter
# is fitted.
#
# The result goes in a separate override file rather than being sed'd
# into the rendered compose: it keeps the generated main file free of
# host-specific surgery, makes "what CEC does this device have" a
# single greppable artifact, and means a bad enumeration degrades to
# "no CEC" instead of "containers will not start".
CEC_OVERRIDE=/home/${USER}/anthias/docker-compose.cec.override.yml
CEC_NODES=()
for node in /dev/cec[0-9]*; do
    [ -c "$node" ] && CEC_NODES+=("$node")
done

if [ ${#CEC_NODES[@]} -gt 0 ]; then
    echo "Passing ${#CEC_NODES[@]} CEC device(s) through: ${CEC_NODES[*]}"
    {
        echo "# Generated by bin/upgrade_containers.sh — do not edit."
        echo "# Lists the CEC adapters found on this host at upgrade time."
        echo "services:"
        for service in anthias-server anthias-celery; do
            echo "  ${service}:"
            echo "    devices:"
            for node in "${CEC_NODES[@]}"; do
                echo "      - \"${node}:${node}\""
            done
        done
    } > "$CEC_OVERRIDE"
else
    # No CEC hardware. Remove any override left by a previous run so a
    # node that has since disappeared cannot block container start.
    echo "No CEC devices found; display power will report 'not available'"
    rm -f "$CEC_OVERRIDE"
fi

COMPOSE_FILES=(-f /home/${USER}/anthias/docker-compose.yml)
if [[ -f "$CEC_OVERRIDE" ]]; then
    COMPOSE_FILES+=(-f "$CEC_OVERRIDE")
fi
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
