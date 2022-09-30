#!/bin/bash

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
    find . ! -path "*/rtmplite/*" -name \*.py -exec pep8 --ignore=E402,E501,E731 {} +
    nosetests -v -a '!fixme'

    if [[ $? -ne 0 ]]; then
        exit 1
    fi
}

prepare
execute_tests

if [ -n "$server_pid" ]; then
    kill $server_pid
fi
