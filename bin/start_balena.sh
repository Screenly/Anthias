#!/bin/bash

SETUP=
VIEWER=
SERVER=
WEBSOCKET=

while [[ $# -gt 0 ]]
do
    case "$1" in
        --setup)
            SETUP=1
            ;;
        --viewer)
            VIEWER=1
            ;;
        --server)
            SERVER=1
            ;;
        --websocket)
            WEBSOCKET=1
            ;;
      *)
            ;;
    esac
    shift
done

run_setup () {
    mkdir -p \
        /data/.config \
        /data/.config/uzbl \
        /data/.screenly \
        /data/screenly \
        /data/screenly_assets

    cp -n /tmp/screenly/ansible/roles/screenly/files/screenly.conf /data/.screenly/screenly.conf
    cp -n /tmp/screenly/ansible/roles/screenly/files/screenly.db /data/.screenly/screenly.db
    cp -n /tmp/screenly/ansible/roles/screenly/files/uzbl-config /data/.config/uzbl/config-screenly

    cp -rf /tmp/screenly/* /data/screenly/

    if [ -n "${OVERWRITE_CONFIG}" ]; then
        echo "Requested to overwrite Screenly config file."
        cp /data/screenly/ansible/roles/screenly/files/screenly.conf "/data/.screenly/screenly.conf"
    fi

    # Set management page's user and password from environment variables,
    # but only if both of them are provided. Can have empty values provided.
    if [ -n "${MANAGEMENT_USER+x}" ] && [ -n "${MANAGEMENT_PASSWORD+x}" ]; then
        sed -i -e "s/^user=.*/user=${MANAGEMENT_USER}/" -e "s/^password=.*/password=${MANAGEMENT_PASSWORD}/" /data/.screenly/screenly.conf
    fi
}

run_viewer () {
    /usr/bin/X 0<&- &>/dev/null &
    /usr/bin/matchbox-window-manager -use_titlebar no -use_cursor no 0<&- &>/dev/null &

    while true; do
        error=$(/usr/bin/xset s off 2>&1 | grep -c "unable to open display")
            if [[ "$error" -eq 0 ]]; then
            break
        fi
        echo "Still continue..."
        sleep 1
    done

    /usr/bin/xset -dpms
    /usr/bin/xset s noblank

    /usr/bin/python /data/screenly/viewer.py
}

run_server () {
    service nginx start

    export RESIN_UUID=${RESIN_DEVICE_UUID}

    /usr/bin/python /data/screenly/server.py
}

run_websocket () {
    /usr/bin/python /data/screenly/websocket_server_layer.py
}

if [[ "$SETUP" = 1 ]]; then
    run_setup
fi

if [[ "$VIEWER" = 1 ]]; then
    run_viewer
fi

if [[ "$SERVER" = 1 ]]; then
    run_server
fi

if [[ "$WEBSOCKET" = 1 ]]; then
    run_websocket
fi
