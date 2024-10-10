#!/bin/bash

ENVIRONMENT=${ENVIRONMENT:-production}

mkdir -p \
    /data/.config \
    /data/.screenly \
    /data/screenly_assets

cp -n /usr/src/app/ansible/roles/screenly/files/screenly.conf /data/.screenly/screenly.conf
cp -n /usr/src/app/ansible/roles/screenly/files/default_assets.yml /data/.screenly/default_assets.yml
cp -n /usr/src/app/ansible/roles/screenly/files/screenly.db /data/.screenly/screenly.db

echo "Running migration..."
python ./bin/migrate.py

if [[ "$ENVIRONMENT" == "development" ]]; then
    flask --app server.py run --debug --reload --host 0.0.0.0 --port 8080
else
    python server.py
fi
