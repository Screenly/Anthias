#!/bin/bash

mkdir -p \
    /data/.config \
    /data/.anthias \
    /data/screenly_assets

cp -n /usr/src/app/ansible/roles/screenly/files/screenly.conf /data/.anthias/screenly.conf
cp -n /usr/src/app/ansible/roles/screenly/files/default_assets.yml /data/.anthias/default_assets.yml
cp -n /usr/src/app/ansible/roles/screenly/files/screenly.db /data/.anthias/screenly.db

if [ -n "${OVERWRITE_CONFIG}" ]; then
    echo "Requested to overwrite Screenly config file."
    cp /usr/src/app/ansible/roles/screenly/files/screenly.conf "/data/.anthias/screenly.conf"
fi

# Set management page's user and password from environment variables,
# but only if both of them are provided. Can have empty values provided.
if [ -n "${MANAGEMENT_USER+x}" ] && [ -n "${MANAGEMENT_PASSWORD+x}" ]; then
    sed -i -e "s/^user=.*/user=${MANAGEMENT_USER}/" -e "s/^password=.*/password=${MANAGEMENT_PASSWORD}/" /data/.anthias/screenly.conf
fi

echo "Running migration..."
python ./bin/migrate.py

python server.py
