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

# Single-worker on purpose: ZmqPublisher.get_instance() in api/views/*
# binds tcp://0.0.0.0:10001, which would EADDRINUSE across multiple
# workers. Hoist the publisher into a sidecar (or move it to the
# Channels layer) before adding `--workers N` here.
UVICORN_BIND_HOST="${LISTEN:-0.0.0.0}"
HTTP_PORT="${PORT:-8080}"
HTTPS_PORT="${HTTPS_PORT:-8443}"

SSL_ENABLED=0
if [[ -n "$SSL_CERTFILE" && -n "$SSL_KEYFILE" \
      && -f "$SSL_CERTFILE" && -f "$SSL_KEYFILE" ]]; then
    SSL_ENABLED=1
    echo "SSL enabled: cert=$SSL_CERTFILE key=$SSL_KEYFILE"
fi

# Trust X-Forwarded-* only from explicitly listed proxies. We deliberately
# do NOT default this to '*' — the IP allowlists in views_files.py rely on
# the TCP peer address, and a wildcard would let any client spoof
# REMOTE_ADDR via X-Forwarded-For. Operators who terminate TLS at a
# reverse proxy can opt in by setting FORWARDED_ALLOW_IPS.
COMMON_ARGS=(
    --host "$UVICORN_BIND_HOST"
    --timeout-keep-alive 30
)
if [[ -n "${FORWARDED_ALLOW_IPS:-}" ]]; then
    COMMON_ARGS+=(
        --proxy-headers
        --forwarded-allow-ips "$FORWARDED_ALLOW_IPS"
    )
fi
if [[ "$ENVIRONMENT" == "development" ]]; then
    COMMON_ARGS+=(--reload --reload-dir /usr/src/app)
fi

if [[ "$ENVIRONMENT" == "development" ]]; then
    echo "Building frontend assets..."
    bun install && bun run build
else
    echo "Generating Django static files..."
    ./manage.py collectstatic --clear --noinput
fi

if [[ "$SSL_ENABLED" == "1" ]]; then
    # Two listeners: plain HTTP on $HTTP_PORT for inter-container traffic
    # (viewer/webview/celery hit anthias-server:8080 over HTTP), and TLS
    # on $HTTPS_PORT for external clients. Without this, enabling SSL
    # would break the webview and viewer because they cannot validate a
    # self-signed cert presented by uvicorn.
    echo "Starting uvicorn HTTP on :$HTTP_PORT (intra-container)..."
    uvicorn anthias_django.asgi:application \
        --port "$HTTP_PORT" \
        "${COMMON_ARGS[@]}" &
    HTTP_PID=$!

    echo "Starting uvicorn HTTPS on :$HTTPS_PORT (external)..."
    uvicorn anthias_django.asgi:application \
        --port "$HTTPS_PORT" \
        --ssl-certfile "$SSL_CERTFILE" \
        --ssl-keyfile "$SSL_KEYFILE" \
        "${COMMON_ARGS[@]}" &
    HTTPS_PID=$!

    trap 'kill "$HTTP_PID" "$HTTPS_PID" 2>/dev/null || true' TERM INT
    # Exit when either listener dies so Docker's restart policy kicks in.
    wait -n "$HTTP_PID" "$HTTPS_PID"
    EXIT_CODE=$?
    kill "$HTTP_PID" "$HTTPS_PID" 2>/dev/null || true
    wait
    exit "$EXIT_CODE"
else
    echo "Starting uvicorn on :$HTTP_PORT..."
    exec uvicorn anthias_django.asgi:application \
        --port "$HTTP_PORT" \
        "${COMMON_ARGS[@]}"
fi
