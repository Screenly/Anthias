#!/bin/bash

# Defensively expose legacy /data/.screenly and /data/screenly_assets
# paths as symlinks if a running setup still has them in DB rows or in
# an older docker-compose file. No-op on clean installs.
/usr/src/app/bin/migrate_in_container_paths.sh

# Fixes permission on /dev/vchiq
chgrp -f video /dev/vchiq
chmod -f g+rwX /dev/vchiq

# Recreate the kernel's ``/dev/video-dec*`` symlinks inside the
# container for boards whose v4l2_request decoders are reachable
# from upstream mpv (RK3399 / Rock Pi 4 today; future Rockchip /
# Allwinner / Amlogic SBCs likely too). Privileged docker passes
# the underlying ``/dev/video*`` char devices through but mounts
# its own ``/dev`` tmpfs without the udev rules that produce the
# decoder symlinks on the host. ffmpeg's ``hevc_v4l2m2m`` /
# ``h264_v4l2m2m`` lookup expects ``/dev/video-dec*`` and dies
# with "Could not find a valid device" otherwise.
#
# We can't run udev inside the container (no privileged
# udevd, and /sys/class/video4linux is read-only via /sys
# bind), but we don't need to — the rule is mechanical: any
# /dev/video* whose /sys/class/video4linux/<name>/name reads as
# a stateless decoder driver gets a symlink. Iterate explicitly
# instead of shelling udev.
for dev_node in /dev/video*; do
    [ -c "$dev_node" ] || continue
    base=$(basename "$dev_node")
    drv_name_file="/sys/class/video4linux/$base/name"
    [ -r "$drv_name_file" ] || continue
    name=$(cat "$drv_name_file" 2>/dev/null)
    # Rockchip / Allwinner / Amlogic stateless decoders. The
    # canonical kernel naming is:
    #
    #   * ``rkvdec`` — Rock Pi 4's RK3399 HEVC + VP9 stateless
    #     decoder (and equivalents on RK3328 / RK356x / RK3588);
    #   * ``rockchip,<soc>-vpu-dec`` — the legacy "VPU" H.264 /
    #     MPEG block, exposed as a separate v4l2 node;
    #   * ``hantro-vpu`` / ``hantro-g*`` — same silicon family,
    #     different vendor-tree naming on a handful of boards;
    #   * ``cedrus`` — Allwinner H6 / H616 stateless decoder.
    #
    # The match list is the explicit prefix set above plus
    # ``*-vpu-dec`` for the rockchip,<soc>-vpu-dec naming. A
    # broader ``*-dec`` catch-all is tempting but would symlink
    # any future v4l2 device that happens to end ``-dec``
    # (encoders' status nodes, vendor diagnostics) into the
    # decoder namespace; the explicit list covers every kernel
    # naming we've shipped and a new SoC adding one entry here
    # is cheap.
    case "$name" in
        rkvdec*|cedrus*|hantro*|*-vpu-dec)
            ln -snf "$dev_node" "/dev/video-dec${base#video}"
            ;;
    esac
done

chown -f viewer /dev/snd/*

# The viewer runs unprivileged (sudo -u viewer below), and Django settings
# init writes anthias.conf when it can't stat an existing one
# (AnthiasSettings.__init__ -> save()). On devices upgraded from an older
# release the config dir and its files (anthias.conf, latest_anthias_sha,
# …) were created by a root-running container, so the viewer user can't
# read or write them and crash-loops on
# `PermissionError: '/data/.anthias/anthias.conf'`. Recursively give the
# viewer ownership of the config dir (root server/celery are unaffected —
# root ignores ownership). -f stays quiet on a fresh install where the dir
# doesn't exist yet (the viewer creates its own on first run).
chown -Rf viewer /data/.anthias

# QtWebEngine state dirs. On upgraded devices the old AnthiasWebview
# tree is left in place — a fresh AnthiasViewer cache is cheap to
# repopulate (the next page load refetches), so we don't bother
# migrating cookies / local-storage across the rename.
mkdir -p /data/.local/share/AnthiasViewer/QtWebEngine \
    /data/.cache/AnthiasViewer \
    /data/.cache/fontconfig \
    /data/.pki

chown -Rf viewer /data/.local/share/AnthiasViewer
chown -Rf viewer /data/.cache/AnthiasViewer/
chown -Rf viewer /data/.cache/fontconfig
chown -Rf viewer /data/.pki

# Qt + dbus + various Linux apps look up XDG_RUNTIME_DIR; without it they
# log warnings and fall back to ad-hoc paths. Provide a per-uid runtime
# dir owned by the viewer user.
VIEWER_UID=$(id -u viewer)
export XDG_RUNTIME_DIR="/run/user/${VIEWER_UID}"
mkdir -p "${XDG_RUNTIME_DIR}"
chown viewer:video "${XDG_RUNTIME_DIR}"
chmod 700 "${XDG_RUNTIME_DIR}"

# Drop empty locale env vars so they don't override defaults that the
# container image (or downstream consumers like Python's `locale`
# module) would otherwise inherit. docker-compose.yml.tmpl wires
# LANG/LANGUAGE/LC_ALL through envsubst, which produces an empty string
# (`LANG=`) when the host has no locale configured; an empty value is
# semantically different from "unset" — it explicitly clobbers anything
# the image set. Unsetting here means QLocale::system() falls back to
# its built-in default and the C++ webview leaves Accept-Language
# unsent (rather than sending an empty / "C" header).
for var in LANG LANGUAGE LC_ALL; do
    if [ -z "${!var-}" ]; then
        unset "$var"
    fi
done

# Temporary workaround for watchdog
touch /tmp/anthias.watchdog
chown viewer /tmp/anthias.watchdog

# For whatever reason Raspbian messes up the sudo permissions
chown -f root:root /usr/bin/sudo
chown -Rf root:root /etc/sudoers.d
chown -Rf root:root /etc/sudo.conf
chown -Rf root:root /usr/lib/sudo
chown -f root:root /etc/sudoers
chmod -f 4755 /usr/bin/sudo

# SIGUSR1 from the viewer is also sent to the container
# Prevent it so that the container does not fail
trap '' 16

# Disable swapping. Path is cgroup v1 only; cgroup v2 hosts (modern
# Debian / Ubuntu / Raspberry Pi OS Bookworm) don't expose it, so guard
# the write to avoid a noisy "No such file or directory" on every boot.
if [ -w /sys/fs/cgroup/memory/memory.swappiness ]; then
    echo 0 > /sys/fs/cgroup/memory/memory.swappiness
fi

# QtWebEngine renders web content at 1 CSS px = 1 physical px by default,
# which makes pages look ~half-size on a 4K TV (forum 6538). Pick a Qt
# scale factor based on the active framebuffer width so the page is laid
# out as if the screen were 1920px wide and then upscaled. Pi/x86 viewer
# images both expose connector state under /sys/class/drm — the first
# line of `modes` is the active/preferred mode. Skip if the user already
# set QT_SCALE_FACTOR explicitly, so a manual override always wins.
if [ -z "${QT_SCALE_FACTOR:-}" ]; then
    SCREEN_WIDTH=""
    for connector in /sys/class/drm/card*-*; do
        [ -d "$connector" ] || continue
        [ "$(cat "$connector/status" 2>/dev/null)" = "connected" ] || continue
        first_mode=$(head -n1 "$connector/modes" 2>/dev/null)
        case "$first_mode" in
            *x*)
                SCREEN_WIDTH="${first_mode%%x*}"
                break
                ;;
        esac
    done
    if [ -n "$SCREEN_WIDTH" ]; then
        # Round to the nearest integer ratio of 1920 (1, 2, 3...) and
        # cap at 4 so a freak EDID can't request 8x.
        SCALE=$(awk -v w="$SCREEN_WIDTH" 'BEGIN {
            s = w / 1920
            if (s < 1.5) print 1
            else if (s < 2.5) print 2
            else if (s < 3.5) print 3
            else print 4
        }')
        if [ "${SCALE:-1}" -gt 1 ]; then
            export QT_SCALE_FACTOR="$SCALE"
            echo "start_viewer: detected ${SCREEN_WIDTH}px screen, QT_SCALE_FACTOR=${SCALE}"
        fi
    fi
fi

# Start viewer.
# sudo resets PATH to its secure_path, so resolve python via the
# absolute venv path instead — `python` on PATH would otherwise hit
# the system interpreter, which has no Anthias deps installed.
# --preserve-env=XDG_RUNTIME_DIR forces sudo to forward the runtime dir
# we just set; -E alone is subject to env_check / env_delete and is not
# guaranteed for XDG_* on Debian's default sudoers.
#
# /dev/dri/renderD128 carries the host's `render` group, whose
# numeric GID is distro-dependent (typically 992 on Debian/Ubuntu,
# 109 elsewhere, 106 on Pi OS Bookworm) and not always present in
# the container's /etc/group. Without membership the `viewer` user
# can open card0 (group `video`, GID 44 — already a member) but
# not the render node. mpv uses the render node for --vo=gpu on
# every Qt 6 board, whether via wayland (cage path: x86 / arm64 /
# pi5) or drm (linuxfb path: pi4-64). Mirror the host GID into
# the container as a synthetic `host-render` group and add
# `viewer` to it; the supplementary group list `sudo -u viewer`
# later resolves from /etc/group then includes render access.
if [ -e /dev/dri/renderD128 ]; then
    render_gid=$(stat -c %g /dev/dri/renderD128)
    if [ "$render_gid" -ne 0 ]; then
        if ! getent group "$render_gid" >/dev/null; then
            groupadd -g "$render_gid" host-render
        fi
        host_render_group=$(getent group "$render_gid" | cut -d: -f1)
        usermod -aG "$host_render_group" viewer
    fi
fi

# Pi 4 (and any future eglfs_kms board) aborts at startup with "no
# screens available" when no display is attached: under full KMS a DRM
# connector only reads "connected" while a panel is present, so Qt's
# eglfs plugin finds no usable screen and exits — before the viewer emits
# its D-Bus handshake, so the container crash-loops on a headless or
# not-yet-negotiated board. This is the eglfs analogue of the linuxfb
# /dev/fb0 wait below; eglfs has no /dev/fb0, so gate on connector status
# instead. Wait here, before the KMS-card detection, so the detection
# sees the connected connector once a display appears; a genuinely
# headless device idles quietly and self-heals on hotplug.
eglfs_has_display() {
    local status_file
    for status_file in /sys/class/drm/card*-*/status; do
        [ -r "$status_file" ] || continue
        # Treat "connected" — and the occasional bridge that reports
        # "unknown" — as present; only an all-"disconnected" board waits.
        case "$(cat "$status_file" 2>/dev/null)" in
            disconnected | '') ;;
            *) return 0 ;;
        esac
    done
    return 1
}
wait_for_eglfs_display() {
    [ "${QT_QPA_PLATFORM:-}" = 'eglfs' ] || return 0
    eglfs_has_display && return 0

    echo "start_viewer: no display connected yet — waiting. Connect or" \
        "power on the screen; the viewer starts automatically once one is present."
    local waited=0
    until eglfs_has_display; do
        sleep 5
        waited=$((waited + 5))
        if [ "$((waited % 60))" -eq 0 ]; then
            echo "start_viewer: still no display connected after ${waited}s; waiting for a display."
        fi
    done
    echo "start_viewer: display connected after ${waited}s — starting the viewer."
}
wait_for_eglfs_display

# Pi 4 renders through Qt's eglfs_kms platform (see Dockerfile.viewer.j2),
# whose JSON config pins the DRM card device. The vc4-drm (display) and
# v3d (render-only) nodes race during probe, so the *display* card is
# /dev/dri/card1 on some boots/images and /dev/dri/card0 on others — the
# v3d node carries no connectors. A hardcoded device (issue #2947) points
# eglfs at the render-only node on the boots where vc4 loses the race; Qt
# then finds no connectors, never takes DRM master, and the device hangs
# on the balena splash forever. Detect the card that actually owns
# connectors at runtime and rewrite the device path before launch.
if [ "$DEVICE_TYPE" = "pi4-64" ] && [ -n "${QT_QPA_EGLFS_KMS_CONFIG:-}" ]; then
    kms_card=""
    # Prefer a card with a *connected* connector; otherwise fall back to
    # any card that exposes connectors at all. The render-only v3d node
    # has no `cardN-<connector>` entries, so this excludes it even on a
    # headless boot where nothing reads as "connected".
    for status_file in /sys/class/drm/card*-*/status; do
        [ -r "$status_file" ] || continue
        connector=$(basename "$(dirname "$status_file")")  # e.g. card1-HDMI-A-1
        card="${connector%%-*}"                            # e.g. card1
        [ -e "/dev/dri/$card" ] || continue
        [ -n "$kms_card" ] || kms_card="$card"
        if [ "$(cat "$status_file" 2>/dev/null)" = "connected" ]; then
            kms_card="$card"
            break
        fi
    done
    if [ -n "$kms_card" ]; then
        echo "start_viewer: eglfs DRM device = /dev/dri/$kms_card"
        # Connector names stay HDMI1/HDMI2 — Qt derives those from the
        # connector type + type-id (the `-N` suffix in sysfs), which is
        # stable on Pi 4 regardless of which card number vc4 landed on.
        cat > "$QT_QPA_EGLFS_KMS_CONFIG" <<EOF
{
  "device": "/dev/dri/$kms_card",
  "hwcursor": false,
  "pbuffers": true,
  "outputs": [
    { "name": "HDMI1", "mode": "1920x1080" },
    { "name": "HDMI2", "mode": "1920x1080" }
  ]
}
EOF
    fi
fi

# Release the Plymouth boot splash before launching the display.
#
# On a normal systemd host — including our Debian/apt install —
# `plymouth-quit.service` runs once boot completes and tells Plymouth to
# drop the display, so whatever takes over (cage's KMS backend, Qt's
# eglfs) finds DRM master free. balenaOS deliberately disables that:
# it ships a `plymouth-disable-containerized.conf` drop-in gating
# plymouth-quit on `ConditionVirtualization=!container`, expecting the
# application to own the handoff. So on balena Plymouth keeps running.
# On x86 that's fatal: efifb->i915 hands Plymouth an early KMS device,
# so it takes DRM *master*; cage then can't (libseat logs "Could not
# make device fd drm master: Device or resource busy"), runs as a
# non-master client, and every atomic commit is rejected — the screen
# freezes on the splash while the scheduler keeps cycling assets
# (the "Swapchain for output 'DP-1' failed test" spam). On Pi/arm64
# Plymouth draws to /dev/fb0 and never takes DRM master, so this is a
# harmless early splash teardown there.
#
# Reproduce what systemd does for us on Debian: ask the *host* systemd
# to start plymouth-quit.service over the host system bus (mounted by
# the `io.balena.features.dbus` label at DBUS_SYSTEM_BUS_ADDRESS). Two
# constraints force this here rather than inside the viewer process:
#   * Authorisation — balenaOS has no polkit, so host systemd grants
#     Manager.StartUnit to uid 0 only. start_viewer.sh runs as root;
#     the `viewer` user the viewer later drops to would be denied.
#   * Ordering — cage takes (or fails to take) DRM master at its own
#     startup and the viewer is cage's child, so quitting Plymouth any
#     later is too late to matter.
# No-op on the Debian/apt install: DBUS_SYSTEM_BUS_ADDRESS is unset and
# the socket is absent, so the guard below short-circuits (and
# plymouth-quit already ran at boot there).
release_boot_splash() {
    local bus="${DBUS_SYSTEM_BUS_ADDRESS#unix:path=}"
    [ -n "${DBUS_SYSTEM_BUS_ADDRESS:-}" ] && [ -S "$bus" ] || return 0

    echo "start_viewer: quitting Plymouth via host systemd to free DRM master"
    if ! dbus-send --system --print-reply \
        --dest=org.freedesktop.systemd1 \
        /org/freedesktop/systemd1 \
        org.freedesktop.systemd1.Manager.StartUnit \
        string:"plymouth-quit.service" string:"replace" >/dev/null 2>&1; then
        echo "start_viewer: StartUnit plymouth-quit failed; continuing"
        return 0
    fi

    # StartUnit is asynchronous (it returns a job path, not a result).
    # plymouth-quit's ExecStart (`plymouth quit`) blocks until plymouthd
    # has exited and dropped DRM master, so poll the unit until the
    # oneshot reaches a terminal state before we hand the display to
    # cage. Bounded so a board where the unit is condition-skipped (the
    # master was never held) can't stall startup.
    local unit state _
    unit=$(dbus-send --system --print-reply --dest=org.freedesktop.systemd1 \
        /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager.GetUnit \
        string:"plymouth-quit.service" 2>/dev/null \
        | awk -F'"' '/object path/{print $2}')
    [ -n "$unit" ] || return 0
    for _ in $(seq 1 15); do
        state=$(dbus-send --system --print-reply --dest=org.freedesktop.systemd1 \
            "$unit" org.freedesktop.DBus.Properties.Get \
            string:"org.freedesktop.systemd1.Unit" string:"ActiveState" 2>/dev/null \
            | awk -F'"' '/string/{print $2}')
        case "$state" in
            active|failed) return 0 ;;
        esac
        sleep 0.2
    done
}
release_boot_splash

# Qt's linuxfb platform (pi2/pi3) opens /dev/fb0 at startup and cannot
# recover if it is absent. Under full KMS (dtoverlay=vc4-kms-v3d) the
# framebuffer only exists while a display is connected, so a headless
# box, a powered-off panel, or a TV slow to negotiate HDMI at boot leaves
# no /dev/fb0 — and Qt doesn't fail cleanly there: it logs "Unable to
# figure out framebuffer device / no screens available" and aborts with
# heap corruption ("malloc(): unaligned tcache chunk detected"). The
# container then crash-loops, spamming the logs, and never settles.
#
# Wait for the framebuffer instead of launching into a guaranteed crash.
# No assumptions about which connector the panel is on or its resolution:
# when a display is (re)connected the KMS driver creates /dev/fb0 and we
# proceed; a genuinely headless device idles here quietly and self-heals
# on hotplug. Only the linuxfb path needs the /dev/fb0 wait — eglfs has
# no /dev/fb0 and is guarded by wait_for_eglfs_display above instead,
# while cage (wayland) tolerates a missing display without crashing — so
# the QT_QPA_PLATFORM guard makes this a no-op for those paths.
wait_for_framebuffer() {
    [ "${QT_QPA_PLATFORM:-}" = 'linuxfb' ] || return 0
    [ -e /dev/fb0 ] && return 0

    echo "start_viewer: no framebuffer (/dev/fb0) yet — waiting for a display." \
        "Connect or power on the screen; the viewer starts automatically once one is present."
    local waited=0
    until [ -e /dev/fb0 ]; do
        sleep 5
        waited=$((waited + 5))
        if [ "$((waited % 60))" -eq 0 ]; then
            echo "start_viewer: still no /dev/fb0 after ${waited}s; waiting for a display."
        fi
    done
    echo "start_viewer: /dev/fb0 present after ${waited}s — starting the viewer."
}
wait_for_framebuffer

# x86 / arm64 / pi5 run under `cage`, a kiosk wlroots compositor.
# cage acquires DRM master as root, exports WAYLAND_DISPLAY for its
# child, and exits when the child exits — so the existing kill -0
# watchdog below still works. The inner sudo drops back to the
# viewer user; WAYLAND_DISPLAY has to be added to --preserve-env to
# survive sudo's env scrub.
#
# Pi 4 falls through to the direct-sudo path (no cage) under
# QT_QPA_PLATFORM=eglfs (#2904: eglfs gives QGraphicsVideoItem a GL
# painter that linuxfb lacks). The V3D 6.0 doesn't have the bandwidth
# to composite cage on top of video at 4K (738 vo drops/30 s under
# cage vs 3-6 on the eglfs + --gpu-context=drm path), so Pi 4 stays off
# cage until either a newer mpv with v4l2request hwdec or a future Pi
# platform lets us re-evaluate. Qt5 boards (pi2/pi3) share the same
# direct-sudo fallback path under linuxfb.
case "$DEVICE_TYPE" in
    x86|arm64|pi5)
    # libseat's default `logind` backend D-Buses into systemd-logind to
    # acquire a session, but containers have no logind session — cage
    # exits with "Could not get primary session for user". Switch to
    # the `builtin` direct-device backend; the viewer container runs
    # privileged so /dev/dri and /dev/input are open to it.
    # WLR_LIBINPUT_NO_DEVICES=1 lets wlroots start without input
    # devices — a digital-signage kiosk has no keyboard or mouse.
    export LIBSEAT_BACKEND=builtin
    export WLR_LIBINPUT_NO_DEVICES=1

    # cage default `-m extend` spans all enumerated DRM outputs,
    # including ones that are physically disconnected — so a Pi user
    # who plugs into the second micro-HDMI port (HDMI-A-2 instead of
    # HDMI-A-1) ends up with cage rendering to a portion of the
    # virtual canvas that lands on the disconnected connector, and a
    # black screen. Trixie ships cage 0.1.x which has no `-o
    # <connector>` flag, but `-m last` restricts output to whichever
    # connector came up most recently — for the boot-time case
    # (which the kernel detects in enumeration order) that's the
    # last connected output rather than the first. Good enough for
    # the single-display kiosk path; dual-head signage is a separate
    # workflow.
    cage_mode=(-m last)

    # cage runs as root (Dockerfile's USER root) and creates the
    # Wayland socket with root:root 0600 perms, so `sudo -u viewer`
    # below can't connect (Qt: "Failed to create wl_display
    # (Permission denied)"). Chown the socket to viewer in cage's
    # child *before* dropping privileges. cage exports WAYLAND_DISPLAY
    # before exec'ing the child, so the path is fully resolved here.
    cage "${cage_mode[@]}" -- bash -c '
        chown viewer "${XDG_RUNTIME_DIR}/${WAYLAND_DISPLAY}" 2>/dev/null || true
        exec sudo \
            --preserve-env=XDG_RUNTIME_DIR,QT_SCALE_FACTOR,PYTHONPATH,WAYLAND_DISPLAY,LANG,LANGUAGE,LC_ALL \
            -E -u viewer \
            dbus-run-session /venv/bin/python -m anthias_viewer
    ' &
    ;;
    *)
    sudo --preserve-env=XDG_RUNTIME_DIR,QT_SCALE_FACTOR,PYTHONPATH,LANG,LANGUAGE,LC_ALL -E -u viewer \
        dbus-run-session /venv/bin/python -m anthias_viewer &
    ;;
esac

# Wait for the viewer
while true; do
  PID=$(pidof python)
  if [ "$?" == '0' ]; then
    break
  fi
  sleep 0.5
done

# If the viewer runs OOM, force the OOM killer to kill this script so the container restarts
echo 1000 > /proc/$$/oom_score_adj

# Exit when the viewer stops
while kill -0 "$PID"; do
  sleep 1
done
