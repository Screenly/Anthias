#!/bin/bash

systemctl start X.service
systemctl start matchbox.service
systemctl start screenly-viewer.service
systemctl start screenly-web.service
systemctl start screenly-websocket_server_layer.service

mkdir -p \
    /data/.config \
    /data/.config/uzbl \
    /data/.screenly \
    /data/screenly \
    /data/screenly_assets

cp -n ansible/roles/screenly/files/screenly.conf /data/.screenly/screenly.conf
cp -n ansible/roles/screenly/files/screenly.db /data/.screenly/screenly.db
cp -n loading.png /data/screenly/loading.png
cp -n ansible/roles/screenly/files/uzbl-config /data/.config/uzbl/config-screenly

# By default docker gives us 64MB of shared memory size but to display heavy
# pages we need more.
umount /dev/shm && mount -t tmpfs shm /dev/shm

chown -R pi:pi /data
