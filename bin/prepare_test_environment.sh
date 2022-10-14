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

mkdir -p /data/.screenly /data/screenly_assets /tmp/USB/cleanup_folder
cp ansible/roles/screenly/files/screenly.db /data/.screenly/
cp ansible/roles/screenly/files/screenly.conf /data/.screenly/
cp tests/assets/asset.mov /tmp/video.mov
curl $OSE_LOGO_URL > /tmp/image.png
cp /tmp/image.png /tmp/USB/image.png
cp /tmp/image.png /tmp/USB/cleanup_folder/image.png
cp tests/config/ffserver.conf /etc/ffserver.conf

if [ "$START_SERVER" = true ]; then
    python server.py &
    sleep 3
fi
