#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euo pipefail

START_SERVER=false

print_usage () {
    echo "Usage: $0 [-s]"
    echo "Example: $0 -s"
    echo
    echo "Options:"
    echo "  -s    run the web server (server.py)"
}

while getopts "sh" arg; do
    case "${arg}" in
        h)
            print_usage
            exit 0
            ;;
        s)
            START_SERVER=true
            ;;
        *)
            print_usage
            exit 0
            ;;
    esac
done

cp tests/assets/asset.mov /tmp/asset.mov
cp static/img/ose-logo.png /tmp/image.png
cp /tmp/image.png /tmp/USB/image.png
cp /tmp/image.png /tmp/USB/cleanup_folder/image.png
cp tests/config/ffserver.conf /etc/ffserver.conf

nohup /opt/ffmpeg/ffserver -f /etc/ffserver.conf > /dev/null 2>&1 &
sleep 3

if [ "$START_SERVER" = true ]; then
    cd /usr/src/app
    python server.py &
    sleep 3
fi
