#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euo pipefail

START_SERVER=false
# TODO: You could just fetch from within the repo instead.
OSE_LOGO_URL='https://github.com/Screenly/screenly-ose/raw/master/static/img/ose-logo.png'

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

if [ ! -d /data/ffmpeg ] || [ ! -f /data/ffmpeg/ffserver ]; then
    cd /data
    git clone https://git.ffmpeg.org/ffmpeg.git ffmpeg
    cd ffmpeg
    git checkout 2ca65fc7b74444edd51d5803a2c1e05a801a6023
    ./configure --disable-x86asm
    make -j4
fi

nohup /data/ffmpeg/ffserver -f /etc/ffserver.conf > /dev/null 2>&1 &
sleep 1

if [ "$START_SERVER" = true ]; then
    cd /usr/src/app
    python server.py &
    sleep 3
fi
