# shellcheck shell=bash
#
# bin/lib/viewer/common.sh — shared viewer-boot helpers, sourced by
# bin/start_viewer.sh. Function definitions only; nothing runs at source
# time. start_viewer.sh calls these in the order the boot needs. The code
# is moved verbatim from the former monolithic start_viewer.sh, so the
# comments explaining each step travel with it.

viewer_migrate_and_fix_devices() {
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
}

viewer_prepare_data_dirs() {
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
    /data/.cache/gstreamer-1.0 \
    /data/.pki

chown -Rf viewer /data/.local/share/AnthiasViewer
chown -Rf viewer /data/.cache/AnthiasViewer/
chown -Rf viewer /data/.cache/fontconfig
# pi3-64's video path links GStreamer; without a writable registry cache
# the viewer rescans every plugin on each launch (slow startup — enough to
# blow the 30s D-Bus handshake budget on the memory-tight 1GB board).
chown -Rf viewer /data/.cache/gstreamer-1.0
chown -Rf viewer /data/.pki

# Qt + dbus + various Linux apps look up XDG_RUNTIME_DIR; without it they
# log warnings and fall back to ad-hoc paths. Provide a per-uid runtime
# dir owned by the viewer user.
VIEWER_UID=$(id -u viewer)
export XDG_RUNTIME_DIR="/run/user/${VIEWER_UID}"
mkdir -p "${XDG_RUNTIME_DIR}"
chown viewer:video "${XDG_RUNTIME_DIR}"
chmod 700 "${XDG_RUNTIME_DIR}"
}

# Qt 6 boards play video through QtMultimedia (QMediaPlayer →
# QAudioOutput), and Debian's Qt 6 Multimedia is compiled with
# PulseAudio as its ONLY audio backend — libQt6Multimedia.so.6 links
# libpulse and has no ALSA code. Without a running PulseAudio server
# QMediaDevices enumerates zero audio outputs, QAudioOutput keeps a
# null device, and every video plays silent (issue #3000). The
# pre-#2905 mpv path talked ALSA directly and needed no sound server,
# which is why this only bit after the QtMultimedia migration.
#
# Run a minimal per-container daemon as the viewer user. The config
# is generated instead of using Debian's default.pa because the stock
# config detects cards via module-udev-detect, which finds nothing in
# a container without udevd (only the auto_null sink appears). One
# module-alsa-card per /proc/asound/cards entry, loaded with
# name=<alsa card id>, names the pulse sinks after the ALSA card
# (e.g. alsa_output.vc4hdmi0.hdmi-stereo) — exactly the CARD=<name>
# discriminator VideoView::resolveAlsaDevice matches the Python
# side's get_alsa_audio_device() spec against, so the existing
# HDMI-port auto-detection and the hdmi/local audio_output setting
# keep working unchanged on top of pulse.
start_pulseaudio() {
    # Qt 5 boards (pi2/pi3) don't ship pulseaudio: GstFbdevMediaPlayer
    # drives alsasink directly.
    command -v pulseaudio > /dev/null || return 0

    # PulseAudio keeps its state (auth cookie) under
    # ~viewer/.config/pulse. On upgraded devices /data/.config was
    # created by a root-running container, so pre-create the subdir
    # and hand it to the viewer — same pattern as the /data/.anthias
    # chown above. (The daemon refuses to start when it can't write
    # its state dir.)
    mkdir -p /data/.config/pulse
    chown -Rf viewer /data/.config/pulse

    pa_config=/tmp/anthias-pulse.pa
    {
        echo '.fail'
        echo 'load-module module-native-protocol-unix'
        # .nofail: a card whose profile can't open right now (e.g.
        # the vc4hdmi of an unplugged HDMI port) must not abort
        # daemon startup — the other cards still have to load.
        echo '.nofail'
        sed -n 's/^ *\([0-9][0-9]*\) \[\([^ ]*\) *\].*/\1 \2/p' \
            /proc/asound/cards \
            | while read -r card_index card_id; do
                echo "load-module module-alsa-card" \
                    "device_id=${card_index} name=${card_id}"
            done
        echo '.fail'
        # Suspend idle sinks so the ALSA PCM is released between
        # videos instead of held open by the daemon for the
        # container's whole lifetime (same as Debian's default.pa).
        echo 'load-module module-suspend-on-idle'
        # Guarantee a default sink (auto_null) even when no card
        # loaded, so clients still connect cleanly instead of
        # erroring — same silent outcome as today, but recoverable
        # by a container restart once audio hardware shows up.
        echo 'load-module module-always-sink'
    } > "$pa_config"

    # Clear the stale pid file a previously SIGKILLed daemon left behind.
    # XDG_RUNTIME_DIR is a persistent path in the container's writable
    # layer, not a per-boot tmpfs like on the host, so the pid file (and
    # socket) survive a container restart or a device reboot. On the next
    # start, --daemonize's self-exec re-runs pa_pid_file_create() and
    # racily reads that pid file as a live pulseaudio — its own pre-exec
    # PID — and aborts with "Daemon already running", leaving video silent
    # (issue #3112). Removing the pid file makes every restart clean; the
    # socket is unlinked by module-native-protocol-unix's own stale check.
    rm -f "${XDG_RUNTIME_DIR}/pulse/pid"

    # --daemonize blocks until the daemon is initialised, so the
    # socket is guaranteed to exist before AnthiasViewer's
    # QMediaDevices first looks for a server. exit-idle-time=-1
    # keeps the daemon alive across the long client-less gaps
    # between videos.
    if ! sudo --preserve-env=XDG_RUNTIME_DIR -E -u viewer \
        pulseaudio --daemonize=yes --exit-idle-time=-1 -nF "$pa_config"; then
        echo "start_viewer: pulseaudio failed to start —" \
            "video will play without audio"
    fi
}

viewer_clean_locale_env() {
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
}

viewer_prepare_runtime() {
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
}

viewer_detect_scale_factor() {
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
}

viewer_setup_render_group() {
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
}

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

launch_direct_sudo() {
    sudo --preserve-env=XDG_RUNTIME_DIR,QT_SCALE_FACTOR,PYTHONPATH,LANG,LANGUAGE,LC_ALL -E -u viewer \
        dbus-run-session /venv/bin/python -m anthias_viewer &
}

viewer_wait_and_supervise() {
# Wait for the viewer
while true; do
  PID=$(pidof python)
  if [ "$?" == '0' ]; then
    break
  fi
  sleep 0.5
done
# pidof may return several space-separated PIDs (the linuxfb video path
# spawns a gst_fbdev_player.py helper alongside the viewer). Keep a
# single numeric PID so `kill -0 "$PID"` and monitor_hdmi_resolution
# don't get a multi-word string that kill rejects (which would exit the
# wait loop immediately and silently disable the HDMI watchdog). pidof
# lists newest first, so the last token is the oldest process — the
# long-lived viewer, launched before any helper.
PID=${PID##* }

# Self-heal the linuxfb 1024x768 HDMI-hotplug latch (issue #3052): on a
# TV power-cycle, re-assert the connector's preferred mode and restart
# the viewer. No-op on eglfs/wayland boards (guarded inside).
monitor_hdmi_resolution "$PID" &

# If the viewer runs OOM, force the OOM killer to kill this script so the container restarts
echo 1000 > /proc/$$/oom_score_adj

# Exit when the viewer stops
while kill -0 "$PID"; do
  sleep 1
done
}
