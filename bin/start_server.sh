#!/bin/bash

ENVIRONMENT=${ENVIRONMENT:-production}

# Defensively expose legacy /data/.screenly and /data/screenly_assets
# paths as symlinks if a running setup still has them in DB rows or in
# an older docker-compose file. No-op on clean installs.
/usr/src/app/bin/migrate_in_container_paths.sh

mkdir -p \
    /data/.config \
    /data/.anthias \
    /data/.anthias/backups \
    /data/anthias_assets

cp -n /usr/src/app/ansible/roles/anthias/files/anthias.conf /data/.anthias/anthias.conf
cp -n /usr/src/app/ansible/roles/anthias/files/default_assets.yml /data/.anthias/default_assets.yml

echo "Running migration..."

# The following block ensures that the migration is transactional and that the
# database is not left in an inconsistent state if the migration fails.

if [ -f /data/.anthias/anthias.db ]; then
    ./manage.py dbbackup --noinput --clean && \
        ./manage.py migrate --fake-initial --noinput || \
        ./manage.py dbrestore --noinput
else
    ./manage.py migrate && \
        ./manage.py dbbackup --noinput --clean
fi

if [[ "$ENVIRONMENT" == "development" ]]; then
    echo "Starting Django development server..."
    bun install && bun run build
    ./manage.py runserver 0.0.0.0:8080
else
    echo "Generating Django static files..."
    ./manage.py collectstatic --clear --noinput
    echo "Starting Gunicorn..."
    python run_gunicorn.py
fi
