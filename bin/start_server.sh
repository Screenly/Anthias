#!/bin/bash

ENVIRONMENT=${ENVIRONMENT:-production}

mkdir -p \
    /data/.config \
    /data/.screenly \
    /data/.screenly/backups \
    /data/screenly_assets

cp -n /usr/src/app/ansible/roles/screenly/files/screenly.conf /data/.screenly/screenly.conf
cp -n /usr/src/app/ansible/roles/screenly/files/default_assets.yml /data/.screenly/default_assets.yml

echo "Running migration..."

# The following block ensures that the migration is transactional and that the
# database is not left in an inconsistent state if the migration fails.

if [ -f /data/.screenly/screenly.db ]; then
    ./manage.py dbbackup --noinput --clean && \
        ./manage.py migrate --fake-initial --noinput || \
        ./manage.py dbrestore --noinput
else
    ./manage.py migrate && \
        ./manage.py dbbackup --noinput --clean
fi

if [[ "$ENVIRONMENT" == "development" ]]; then
    echo "Starting Django development server (Daphne)..."
    daphne -b 0.0.0.0 -p 8000 anthias_django.asgi:application
else
    echo "Generating Django static files..."
    ./manage.py collectstatic --clear --noinput
    echo "Starting Daphne ASGI server..."
    daphne -b 0.0.0.0 -p 8000 anthias_django.asgi:application
fi
