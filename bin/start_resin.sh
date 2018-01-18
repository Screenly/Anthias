#!/bin/bash

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

if [ -n "${OVERWRITE_CONFIG}" ]; then
    echo "Requested to overwrite Screenly config file."
    cp ansible/roles/screenly/files/screenly.conf "/data/.screenly/screenly.conf"
fi

# Make sure the right permission is set
chown -R pi:pi /data

# Set management page's user and password from environment variables,
# but only if both of them are provided. Can have empty values provided.
if [ -n "${MANAGEMENT_USER+x}" ] && [ -n "${MANAGEMENT_PASSWORD+x}" ]; then
    sed -i -e "s/^user=.*/user=${MANAGEMENT_USER}/" -e "s/^password=.*/password=${MANAGEMENT_PASSWORD}/" /data/.screenly/screenly.conf
fi

sed -i "/\[Service\]/ a\Environment=RESIN_UUID=${RESIN_DEVICE_UUID}" /etc/systemd/system/screenly-web.service

systemctl start X.service
systemctl start matchbox.service
systemctl start screenly-viewer.service
systemctl start screenly-web.service
systemctl start screenly-websocket_server_layer.service

# By default docker gives us 64MB of shared memory size but to display heavy
# pages we need more.
umount /dev/shm && mount -t tmpfs shm /dev/shm

# Send logs to stdout
journalctl -f -a
