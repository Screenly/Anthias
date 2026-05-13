#!/bin/bash

# Defensively expose legacy /data/.screenly and /data/screenly_assets
# paths as symlinks if a running setup still has them in DB rows or in
# an older docker-compose file. No-op on clean installs.
/usr/src/app/bin/migrate_in_container_paths.sh

# Fixes permission on /dev/vchiq
chgrp -f video /dev/vchiq
chmod -f g+rwX /dev/vchiq

# Set permission for sha file
chown -f viewer /dev/snd/*
chown -f viewer /data/.anthias/latest_anthias_sha

# Fixes caching in QTWebEngine
mkdir -p /data/.local/share/AnthiasWebview/QtWebEngine \
    /data/.cache/AnthiasWebview \
    /data/.cache/fontconfig \
    /data/.pki

chown -Rf viewer /data/.local/share/AnthiasWebview
chown -Rf viewer /data/.cache/AnthiasWebview/
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

# x86 / arm64 / pi5 run under `cage`, a kiosk wlroots compositor.
# cage acquires DRM master as root, exports WAYLAND_DISPLAY for its
# child, and exits when the child exits — so the existing kill -0
# watchdog below still works. The inner sudo drops back to the
# viewer user; WAYLAND_DISPLAY has to be added to --preserve-env to
# survive sudo's env scrub.
#
# Pi 4 falls through to the legacy direct-sudo path that runs under
# QT_QPA_PLATFORM=linuxfb. The V3D 6.0 doesn't have the bandwidth
# to composite cage on top of video at 4K (738 vo drops/30 s under
# cage vs 3-6 on the linuxfb + --gpu-context=drm path), so Pi 4
# stays on linuxfb until either a newer mpv with v4l2request hwdec
# or a future Pi platform lets us re-evaluate. Qt5 boards (pi2/pi3)
# share the same direct-sudo fallback path.
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
    # cage runs as root (Dockerfile's USER root) and creates the
    # Wayland socket with root:root 0600 perms, so `sudo -u viewer`
    # below can't connect (Qt: "Failed to create wl_display
    # (Permission denied)"). Chown the socket to viewer in cage's
    # child *before* dropping privileges. cage exports WAYLAND_DISPLAY
    # before exec'ing the child, so the path is fully resolved here.
    cage -- bash -c '
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
