#!/bin/bash

mkdir -p \
    /data/.config \
    /data/.screenly \
    /data/screenly \
    /data/screenly_assets

cp -n ansible/roles/screenly/files/screenly.conf /data/.screenly/screenly.conf
cp -n ansible/roles/screenly/files/screenly.db /data/.screenly/screenly.db
cp -n loading.png /data/screenly/loading.png

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

cat << EOF > /etc/systemd/system/screenly-server.service
[Unit]
Description=Screenly Web UI
After=network-online.target

[Service]
WorkingDirectory=/home/pi/screenly
User=pi

Environment=HOME=/data
Environment=PYTHONPATH=/home/pi/screenly
Environment=LISTEN=0.0.0.0
Environment=SWAGGER_HOST=ose.demo.screenlyapp.com
Environment=RESIN_UUID=${RESIN_DEVICE_UUID}

ExecStartPre=/usr/bin/python /home/pi/screenly/bin/wait.py
ExecStart=/usr/bin/python /home/pi/screenly/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat << EOF > /etc/systemd/system/screenly-viewer.service
[Unit]
Description=Screenly Viewer
After=screenly-server.service

[Service]
WorkingDirectory=/home/pi/screenly
User=pi

Environment=HOME=/data
Environment=QT_QPA_EGLFS_FORCE888=1
Environment=QT_QPA_EGLFS_PHYSICAL_WIDTH=154
Environment=QT_QPA_EGLFS_PHYSICAL_HEIGHT=86
Environment=QT_QPA_EGLFS_WIDTH=1280
Environment=QT_QPA_EGLFS_HEIGHT=1024
Environment=QT_QPA_PLATFORM=eglfs
Environment=DISABLE_UPDATE_CHECK=True

ExecStart=/usr/bin/dbus-run-session /usr/bin/python /home/pi/screenly/viewer.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

cat << EOF > /etc/systemd/system/screenly-websocket.service
[Unit]
Description=Websocket Server layer
After=screenly-server.service

[Service]
WorkingDirectory=/home/pi/screenly
User=pi

Environment=HOME=/data

ExecStart=/usr/bin/python /home/pi/screenly/websocket_server_layer.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl start screenly-server.service
systemctl start screenly-viewer.service
systemctl start screenly-websocket.service

# By default docker gives us 64MB of shared memory size but to display heavy
# pages we need more.
umount /dev/shm && mount -t tmpfs shm /dev/shm

# Send logs to stdout
journalctl -f -a
