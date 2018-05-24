#!/bin/bash

# SUGUSR1 from the viewer is also sent to the container
# Prevent it so that the container does not fall
trap '' 16

# Start X
rm -f /tmp/.X0-lock
/usr/bin/X &
export DISPLAY=:0.0

# Waiting for X11
while ! xdpyinfo >/dev/null 2>&1; do
  sleep 0.5
done

su - pi -c " /usr/bin/matchbox-window-manager -use_titlebar no -use_cursor no &"

su - pi -c "cd /home/pi/screenly && dbus-run-session python viewer.py &"

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
