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

UVICORN_BIND_HOST="${LISTEN:-0.0.0.0}"
UVICORN_BIND_PORT="${PORT:-8080}"

# Trust X-Forwarded-* only from explicitly listed proxies. We deliberately
# do NOT default this to '*' — the IP allowlists in views_files.py rely on
# the TCP peer address, and a wildcard would let any client spoof
# REMOTE_ADDR via X-Forwarded-For. Operators who terminate TLS at a
# reverse proxy (e.g. the Caddy sidecar bin/enable_ssl.sh installs) get
# this set automatically via the compose override.
UVICORN_PROXY_ARGS=()
if [[ -n "${FORWARDED_ALLOW_IPS:-}" ]]; then
    UVICORN_PROXY_ARGS=(
        --proxy-headers
        --forwarded-allow-ips "$FORWARDED_ALLOW_IPS"
    )
fi

if [[ "$ENVIRONMENT" == "development" ]]; then
    echo "Building frontend assets..."
    bun install && bun run build
    echo "Starting uvicorn (development, --reload)..."
    exec uvicorn anthias_django.asgi:application \
        --host "$UVICORN_BIND_HOST" \
        --port "$UVICORN_BIND_PORT" \
        --timeout-keep-alive 30 \
        --reload \
        --reload-dir /usr/src/app \
        "${UVICORN_PROXY_ARGS[@]}"
else
    echo "Generating Django static files..."
    ./manage.py collectstatic --clear --noinput
    echo "Starting uvicorn..."
    exec uvicorn anthias_django.asgi:application \
        --host "$UVICORN_BIND_HOST" \
        --port "$UVICORN_BIND_PORT" \
        --timeout-keep-alive 30 \
        "${UVICORN_PROXY_ARGS[@]}"
fi
