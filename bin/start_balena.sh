#!/bin/bash

run_setup () {
    mkdir -p \
        /data/.config \
        /data/.screenly \
        /data/screenly \
        /data/screenly_assets

    cp -n /tmp/screenly/ansible/roles/screenly/files/screenly.conf /data/.screenly/screenly.conf
    cp -n /tmp/screenly/ansible/roles/screenly/files/default_assets.yml /data/.screenly/default_assets.yml
    cp -n /tmp/screenly/ansible/roles/screenly/files/screenly.db /data/.screenly/screenly.db

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

    /usr/bin/python /data/screenly/bin/migrate.py
}

run_viewer () {
    # By default docker gives us 64MB of shared memory size but to display heavy
    # pages we need more.
    umount /dev/shm && mount -t tmpfs shm /dev/shm

    while true; do

        error=$(curl screenly-server:80 2>&1 | grep -c "Failed to connect")
            if [[ "$error" -eq 0 ]]; then
            break
        fi

        echo "Still continue..."
        sleep 1
    done

    trap '' 16

    cd /data/screenly && QT_QPA_EGLFS_FORCE888=1 HOME=/data dbus-run-session python viewer.py &

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
}

run_server () {
    service nginx start

    export RESIN_UUID=${RESIN_DEVICE_UUID}

    cd /data/screenly
    /usr/bin/python server.py
}

run_websocket () {
    cd /data/screenly
    /usr/bin/python websocket_server_layer.py
}

run_celery () {
    cd /data/screenly
    celery worker -A server.celery -B -n worker@screenly --loglevel=info --schedule /tmp/celerybeat-schedule
}

if [[ "$SCREENLYSERVICE" = "server" ]]; then
    run_setup
    run_server
fi

if [[ "$SCREENLYSERVICE" = "viewer" ]]; then
    run_viewer
fi

if [[ "$SCREENLYSERVICE" = "websocket" ]]; then
    run_websocket
fi

if [[ "$SCREENLYSERVICE" = "celery" ]]; then
    run_celery
fi
