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
sudo --preserve-env=XDG_RUNTIME_DIR,QT_SCALE_FACTOR -E -u viewer \
    dbus-run-session /venv/bin/python -m viewer &

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
