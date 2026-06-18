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
# Low-RAM gate. Boards with < 1.5 GiB MemTotal (Pi 2/Pi 3 1GB, Pi 4 1GB,
# Rock Pi 4 1GB, generic-arm64 SBCs in that class) can't keep two
# QtWebEngine renderer processes resident *and* play 1080p+ video
# without OOM-thrashing through swap. The viewer reads this env to
# drop into single-WebEngineView mode (no preloaded crossfade); the
# asset processor reads it via Redis (host:total_mem_kb) and rejects
# uploads above 1080p. Threshold is 1.5 GiB so 1 GB SKUs land below
# and 2 GB SKUs sit above; both 1024 MB and 2048 MB boards exist in
# the supported fleet.
if [ "${TOTAL_MEMORY_KB:-0}" -lt 1572864 ]; then
    export ANTHIAS_LOW_RAM=1
else
    export ANTHIAS_LOW_RAM=0
fi
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

cat /home/${USER}/anthias/docker-compose.yml.tmpl \
    | envsubst \
    > /home/${USER}/anthias/docker-compose.yml

# CEC device routing. Pi 1-4 reaches libcec via /dev/vchiq
# (closed-firmware VideoCore IV), which is what the template's
# `devices:` block bind-mounts. Pi 5 and mainline-KMS x86/arm64 boards
# expose v4l2 CEC adapters at /dev/cec0 instead (Pi 5 also exposes
# /dev/cec1 for the second HDMI output, so we map both). docker
# compose's `devices:` fails container start if a listed host node is
# missing, so we surgically rewrite the rendered mount per device
# type — and on x86/arm64 we only swap in /dev/cec0 if the host
# actually has it (a box without an HDMI-CEC adapter keeps the
# pre-fix behaviour of dropping the bind mount entirely). Fixes
# the "CEC error" toast on Pi 5 reported in issue #2863.
case "$DEVICE_TYPE" in
    pi4-64|pi5)
        CEC_DEV=""
        if command -v cec-ctl >/dev/null 2>&1; then
            for DEV in /dev/cec0 /dev/cec1; do
                [ -e "$DEV" ] || continue
                PHYS_ADDR=$(cec-ctl -d "$DEV" --playback --logical-address 2>/dev/null \
                    | grep "Physical Address" | awk -F: '{print $2}' | xargs)
                if [ -n "$PHYS_ADDR" ] && [ "$PHYS_ADDR" != "f.f.f.f" ]; then
                    CEC_DEV="$DEV"
                    break
                fi
            done
        fi

        if [ -n "$CEC_DEV" ]; then
            # libcec solo prueba /dev/cec0 — no enumera /dev/cec1
            # aunque sea el puerto realmente conectado a la TV
            # (confirmado en hardware: con solo /dev/cec1 montado con
            # su propio nombre, cec.init() tira "No default adapter
            # found"). Remapeamos el puerto que esté vivo al path fijo
            # /dev/cec0 dentro del contenedor, sin importar a qué
            # micro-HDMI físico corresponda en el host.
            sed -i "s|^\([[:space:]]*\)- \"/dev/vchiq:/dev/vchiq\"\$|\1- \"$CEC_DEV:/dev/cec0\"|" \
                /home/${USER}/anthias/docker-compose.yml
        else
            # cec-ctl no está en el host, o ningún puerto reportó
            # dirección física (TV apagada durante el upgrade, por
            # ejemplo): mantenemos el comportamiento anterior —
            # montar los dos con su nombre real. Si el puerto vivo
            # resulta ser /dev/cec1, va a seguir sin funcionar hasta
            # el próximo upgrade que sí pueda detectarlo; es una
            # limitación conocida de este fallback degradado.
            sed -i 's|^\([[:space:]]*\)- "/dev/vchiq:/dev/vchiq"$|\1- "/dev/cec0:/dev/cec0"\n\1- "/dev/cec1:/dev/cec1"|' \
                /home/${USER}/anthias/docker-compose.yml
        fi
        ;;
    x86|arm64)
        ...
        ;;
esac

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
