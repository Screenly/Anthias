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

# Used on two paths below: the cleanup after a successful upgrade, and
# the narrower reclaim after a failed pull. The existence check at each
# call site covers the operator who pulled only this script down instead
# of updating the whole checkout.
PURGE_SCRIPT="${SCRIPT_DIR}/purge_stale_images.sh"

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
#   * srly-ose-wifi-connect — the pre-rebrand container for the old
#     balena-os wifi-connect integration. The current
#     anthias-wifi-connect container runs the Screenly-Labs tool and is
#     defined in the compose file, so it must NOT be listed here: `up
#     -d` would recreate it on every upgrade and the `docker rm -f`
#     above it would tear down a portal that happens to be live.
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
    anthias-nginx anthias-websocket \
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

# No CEC device passthrough is needed, on any board.
#
# HDMI-CEC is driven by the viewer container, which is `privileged: true`
# in every compose template and therefore already sees every /dev/cec*
# node. anthias-server and anthias-celery ask it over the Redis command
# bus (see src/anthias_server/lib/cec_client.py).
#
# An earlier iteration of this script enumerated the host's adapters here
# and generated a compose override for server/celery. That works on this
# install path but is impossible on balena — its compose file is baked
# into the release from a workstation, nothing on-device can enumerate
# the host, and a statically listed node that turns out to be absent
# stops the container from starting. Routing through the viewer supports
# both deployments with no device wiring and no OTA upgrade risk.
#
# A stale override from that iteration is removed so it cannot keep
# pinning nodes that the containers no longer need.
rm -f /home/${USER}/anthias/docker-compose.cec.override.yml

COMPOSE_FILES=(-f /home/${USER}/anthias/docker-compose.yml)
SSL_OVERRIDE=/home/${USER}/anthias/docker-compose.ssl.override.yml
if [[ -f "$SSL_OVERRIDE" ]]; then
    COMPOSE_FILES+=(-f "$SSL_OVERRIDE")
fi

# On a memory-tight board, free RAM before pulling. `docker compose pull`
# extracts the new image layers while the current stack is still resident;
# on boards under LOW_RAM_THRESHOLD_KB (1.5 GiB — Pi 2, the 1 GB Pi 3s and
# Rock Pi 4, and the 512 MB Pi 3 A+) the QtWebEngine viewer's footprint
# plus that extraction exhausts RAM+swap and the board hard-resets
# mid-pull — reproduced on the 512 MB Pi 3 A+ during the 2026.09.0
# burn-in, where the same pull only completed once the viewer was stopped.
# So stop the two heavy services (the Chromium viewer and the celery image
# workers) first; the `up -d` below restarts them on the freshly pulled
# images. anthias-server and redis stay up so the admin UI keeps answering
# and a failed pull doesn't black the screen. install.sh does the same for
# its host-side upgrade work (issue #3165); capable boards (>= 1.5 GiB)
# keep pulling with the stack live to minimise downtime.
#
# `stop` — not `down` — on purpose: it leaves the containers defined, so
# their `restart: always` policy still brings them back on the next boot.
# That matters because the reboot-required branch below exits before the
# `up -d`, and nothing else runs `docker compose up` at boot; a `down`
# there would remove the containers and leave the board dark until a
# manual bring-up.
LOW_RAM_THRESHOLD_KB=1572864  # 1.5 GiB; mirrors anthias_common.board.LOW_RAM_THRESHOLD_KB
if [ "$MODE" = "pull" ] && [ "${TOTAL_MEMORY_KB:-0}" -lt "$LOW_RAM_THRESHOLD_KB" ]; then
    echo "Low-RAM board (${TOTAL_MEMORY_KB} kB total): stopping the viewer and" \
         "celery before pulling so the image extraction doesn't OOM the board."
    sudo -E docker compose "${COMPOSE_FILES[@]}" stop anthias-viewer anthias-celery || true
fi

# A plain `compose pull` skips profiled services, so anthias-wifi-connect
# would otherwise be fetched implicitly by the `up --no-start` further
# down, after the signature check below had already run without it.
# Activating the profile here pulls it with everything else, and that
# later `up` then reuses the verified local image instead of pulling.
PULL_PROFILES=()
if [[ "$MODE" = "pull" ]]; then
    PULL_PROFILES=(--profile wifi-connect)
fi
# Bring back whatever the low-RAM branch above stopped. `start` reuses
# the existing containers, which still point at the images they were
# created from rather than at anything just pulled, so this returns the
# device to the release it was already running.
function restore_stopped_services() {
    sudo -E docker compose "${COMPOSE_FILES[@]}" \
        start anthias-viewer anthias-celery || true
}

PULL_STATUS=0
sudo -E docker compose "${COMPOSE_FILES[@]}" "${PULL_PROFILES[@]}" "${MODE}" \
    || PULL_STATUS=$?
if [[ "${PULL_STATUS}" -ne 0 ]]; then
    # A `${MODE}` that died partway leaves the bytes it had already
    # fetched in containerd's ingest directory. Nothing collects them:
    # they are invisible to `docker images`, they do not expire, and the
    # cleanup at the end of this script never runs on this path. So a
    # device upgrading *because* it is short of space loses a few hundred
    # MB to every failed attempt and fails earlier on the next one —
    # a ratchet, not a plateau (issue #3410).
    #
    # `--untagged-only` is what makes reclaiming safe this early.
    # The full sweep would also drop the superseded images, and on a
    # failed upgrade those are the release the device is still running —
    # which is precisely why the cleanup is otherwise deferred to the end.
    # Restricted to unreferenced, untagged content, there is nothing left
    # for it to take away.
    echo "The ${MODE} failed; reclaiming what it left behind." >&2
    if [[ -f "${PURGE_SCRIPT}" ]]; then
        bash "${PURGE_SCRIPT}" --untagged-only || true
    fi

    restore_stopped_services
    exit "${PULL_STATUS}"
fi

# Refuse to run pulled images their publisher didn't sign: ours, and the
# upstream wifi-connect image (see verify_image_signatures.sh). The check
# goes against what was just pulled and before anything is started from
# it, so a failure leaves the device on the release it was already
# running. Locally built images (MODE=build) have no signature to check.
# ANTHIAS_SKIP_IMAGE_VERIFICATION=1 is the escape hatch for an operator
# who deliberately runs images of their own; nothing in Anthias sets it.
if [[ "$MODE" = "pull" ]]; then
    if [[ "${ANTHIAS_SKIP_IMAGE_VERIFICATION:-}" = "1" ]]; then
        echo "WARNING: ANTHIAS_SKIP_IMAGE_VERIFICATION=1 is set; starting" \
             "the pulled images without checking their signatures." >&2
    else
        mapfile -t IMAGES < <(
            sudo -E docker compose "${COMPOSE_FILES[@]}" \
                "${PULL_PROFILES[@]}" config --images
        )
        VERIFY_SCRIPT="${SCRIPT_DIR}/verify_image_signatures.sh"
        if [[ ! -x "${VERIFY_SCRIPT}" ]] \
            || ! "${VERIFY_SCRIPT}" "${IMAGES[@]}"; then
            echo "Refusing to start images that failed signature" \
                 "verification. The device stays on its current release." >&2
            restore_stopped_services
            exit 1
        fi
    fi
fi

if [ -f /var/run/reboot-required ]; then
    exit 0
fi

# --remove-orphans sweeps containers that linger after a service is
# renamed or removed from the compose file (e.g. legacy run-NNN sidecar
# instances left over from earlier `docker compose run` invocations).
# Without it `up -d` only logs a warning and leaves the orphans running,
# which is confusing on a `docker ps` audit later.
sudo -E docker compose "${COMPOSE_FILES[@]}" up -d --remove-orphans

# anthias-wifi-connect sits behind a compose profile, so the `up -d`
# above deliberately skips it: starting the captive portal is the
# systemd unit's job, and only after its ExecCondition has confirmed
# the device is actually offline. Running it from here would raise an
# access point on every upgrade, which on a WiFi-connected screen
# means losing the network it is managed over.
#
# But the unit starts it with `docker start`, so the container has to
# EXIST. `--no-start` creates it (and recreates it when the image or
# the service definition changed) without running it. Naming the
# service explicitly is what activates its profile.
#
# Best effort: a device with no wifi-connect service in its compose
# file (MANAGE_NETWORK=No, or a partial upgrade) must not fail the
# whole upgrade here.
sudo -E docker compose "${COMPOSE_FILES[@]}" --profile wifi-connect \
    up -d --no-start anthias-wifi-connect || \
    echo "Could not pre-create anthias-wifi-connect; WiFi setup will be" \
         "unavailable until the next upgrade."

# Drop the images the release we just replaced was running on. Deliberately
# the last thing we do, and only once the new stack is up, so a pull or a
# start that failed above still leaves the previous images to fall back on.
# Best effort: losing the cleanup must not fail the upgrade itself.
if [ -f "${PURGE_SCRIPT}" ]; then
    bash "${PURGE_SCRIPT}" || true
fi
