#!/bin/bash

# Fixes permission on /dev/vchiq
chgrp -f video /dev/vchiq
chmod -f g+rwX /dev/vchiq

# Set permission for sha file
chown -f viewer /dev/snd/*
chown -f viewer /data/.screenly/latest_anthias_sha

# Fixes caching in QTWebEngine
mkdir -p /data/.local/share/ScreenlyWebview/QtWebEngine \
    /data/hotspot \
    /data/.cache/ScreenlyWebview \
    /data/.pki

chown -Rf viewer /data/.local/share/ScreenlyWebview
chown -Rf viewer /data/.cache/ScreenlyWebview/
chown -Rf viewer /data/.pki
chown -Rf viewer /data/hotspot

# Temporary workaround for watchdog
touch /tmp/screenly.watchdog
chown viewer /tmp/screenly.watchdog

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

# Disable swapping
echo 0 >  /sys/fs/cgroup/memory/memory.swappiness

# TODO: Only run X11 if it's an x86 device.

# Clean up any stale X server processes and lock files
pkill Xorg
rm -f /tmp/.X0-lock /tmp/.X11-unix/X0

# Start X server with dummy video driver
export DISPLAY=:0
Xorg "$DISPLAY" -s 0 dpms &
XORG_PID=$!

# Wait for X server to be ready with timeout
TIMEOUT=30
TIMEOUT_COUNT=0
while [ $TIMEOUT_COUNT -lt $TIMEOUT ]; do
    if xset -display :0 q > /dev/null 2>&1; then
        echo "X server is ready"
        break
    fi

    # Check if X server process is still running
    if ! kill -0 $XORG_PID 2>/dev/null; then
        echo "X server failed to start"
        exit 1
    fi

    echo "Waiting for X server to be ready (${TIMEOUT_COUNT}/${TIMEOUT}s)"
    sleep 1
    TIMEOUT_COUNT=$((TIMEOUT_COUNT + 1))
done

if [ $TIMEOUT_COUNT -eq $TIMEOUT ]; then
    echo "X server failed to start within $TIMEOUT seconds"
    exit 1
fi

# Now that X is ready, configure display settings
xset -display "$DISPLAY" s off
xset -display "$DISPLAY" s noblank
xset -display "$DISPLAY" -dpms

# Start viewer
sudo -E -u viewer dbus-run-session python -m viewer &
VIEWER_PID=$!

# Wait for the viewer with timeout
TIMEOUT=30
TIMEOUT_COUNT=0
while [ $TIMEOUT_COUNT -lt $TIMEOUT ]; do
    if kill -0 $VIEWER_PID 2>/dev/null; then
        break
    fi
    echo "Waiting for viewer to start (${TIMEOUT_COUNT}/${TIMEOUT}s)"
    sleep 1
    TIMEOUT_COUNT=$((TIMEOUT_COUNT + 1))
done

if [ $TIMEOUT_COUNT -eq $TIMEOUT ]; then
    echo "Viewer failed to start within $TIMEOUT seconds"
    exit 1
fi

# If the viewer runs OOM, force the OOM killer to kill this script so the container restarts
echo 1000 > /proc/$$/oom_score_adj

# Exit when the viewer stops
while kill -0 "$VIEWER_PID"; do
    sleep 1
done
