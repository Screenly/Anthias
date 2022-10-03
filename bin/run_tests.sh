#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euo pipefail

prepare () {
    mkdir -p ~/.screenly /tmp/USB/cleanup_folder
    curl https://www.screenly.io/upload/ose-logo.png > /tmp/image.png
    cp /tmp/image.png /tmp/USB/image.png
    cp /tmp/image.png /tmp/USB/cleanup_folder/image.png
    cp tests/config/ffserver.conf /etc/ffserver.conf
    python server.py &
    server_pid=$!
    sleep 3
}

execute_tests () {
    nosetests -v -a '!fixme'
}

prepare
execute_tests

if [ -n "$server_pid" ]; then
    kill $server_pid
fi
