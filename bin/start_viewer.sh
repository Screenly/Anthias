#!/bin/bash

# Fixes permission on /dev/vchiq
chgrp video /dev/vchiq
chmod g+rwX /dev/vchiq

# Set permission for sha file
chown viewer /dev/snd/*
chown viewer /data/.screenly/latest_screenly_sha

# Fixes caching in QTWebEngine
mkdir -p /data/.local/share/ScreenlyWebview/QtWebEngine \
    /data/.cache/ScreenlyWebview \
    /data/.pki
chown -R viewer /data/.local/share/ScreenlyWebview
chown -R viewer /data/.cache/ScreenlyWebview/
chown -R viewer /data/.pki

# Temporary workaround for watchdog
touch /tmp/screenly.watchdog
chown viewer /tmp/screenly.watchdog

# For whatever reason Raspbian messes up the sudo permissions
chown root:root /usr/bin/sudo
chown -R root:root /etc/sudoers.d
chown -R root:root /etc/sudo.conf
chown -R root:root /usr/lib/sudo
chown root:root /etc/sudoers
chmod 4755 /usr/bin/sudo

# SUGUSR1 from the viewer is also sent to the container
# Prevent it so that the container does not fail
trap '' 16

sudo -E -u viewer dbus-run-session python viewer.py &

# Waiting for the viewer
while true; do
  PID=$(pidof python)
  if [ "$?" == '0' ]; then
    break
  fi
  sleep 0.5
done

# Exit when the viewer falls
while kill -0 "$PID"; do
  sleep 1
done
