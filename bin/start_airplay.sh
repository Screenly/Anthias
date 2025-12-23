#!/bin/bash
set -e

# Start D-Bus daemon for Avahi
mkdir -p /var/run/dbus
dbus-daemon --system --nofork &
sleep 1

# Start Avahi daemon for Bonjour/mDNS discovery
avahi-daemon --no-drop-root &
sleep 2

echo "Starting AirPlay server..."
exec python3 /usr/src/app/airplay/server.py
