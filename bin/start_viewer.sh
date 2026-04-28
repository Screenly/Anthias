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
    /data/.pki

chown -Rf viewer /data/.local/share/AnthiasWebview
chown -Rf viewer /data/.cache/AnthiasWebview/
chown -Rf viewer /data/.pki

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

# Disable swapping
echo 0 >  /sys/fs/cgroup/memory/memory.swappiness

# Start viewer.
# sudo resets PATH to its secure_path, so resolve python via the
# absolute venv path instead — `python` on PATH would otherwise hit
# the system interpreter, which has no Anthias deps installed.
sudo -E -u viewer dbus-run-session /venv/bin/python -m viewer &

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
