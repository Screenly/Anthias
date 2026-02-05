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
    uv run python manage.py dbbackup --noinput --clean && \
        uv run python manage.py migrate --fake-initial --noinput || \
        uv run python manage.py dbrestore --noinput
else
    uv run python manage.py migrate && \
        uv run python manage.py dbbackup --noinput --clean
fi

if [[ "$ENVIRONMENT" == "development" ]]; then
    echo "Starting Django development server..."
    npm install && npm run build
    uv run python manage.py runserver 0.0.0.0:8080
else
    echo "Generating Django static files..."
    uv run python manage.py collectstatic --clear --noinput
    echo "Starting Gunicorn..."
    uv run python run_gunicorn.py
fi
