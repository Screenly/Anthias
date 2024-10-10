#!/bin/bash

ENVIRONMENT=${ENVIRONMENT:-production}

mkdir -p \
    /data/.config \
    /data/.screenly \
    /data/screenly_assets

cp -n /usr/src/app/ansible/roles/screenly/files/screenly.conf /data/.screenly/screenly.conf
cp -n /usr/src/app/ansible/roles/screenly/files/default_assets.yml /data/.screenly/default_assets.yml

echo "Running migration..."

# The following block ensures that the migration is transactional and that the
# database is not left in an inconsistent state if the migration fails.
cp /data/.screenly/screenly.db /data/.screenly/backup.db
./manage.py migrate --fake-initial --database=backup
cp /data/.screenly/backup.db /data/.screenly/screenly.db

if [[ "$ENVIRONMENT" == "development" ]]; then
    echo "Starting Django development server..."
    ./manage.py runserver 0.0.0.0:8080
else
    echo "Generating Django static files..."
    ./manage.py collectstatic --clear --noinput
    echo "Starting Gunicorn..."
    python run_gunicorn.py
fi
