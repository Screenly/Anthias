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

# Branding for the WiFi setup portal, which runs in its own container
# from an image that is not ours and has no access to this one's files.
# The shared volume is the only thing the two have in common, so the
# assets are staged here and WIFI_CONNECT_BRAND_CSS / _LOGO point at
# them (see the compose templates).
#
# Here rather than in ansible because ansible does not run on balena,
# and a portal that is Anthias on one platform and upstream's blue on
# the other is not a thing to explain to a customer. It also means the
# logo has one source -- the splash page's own file -- instead of a
# copy under ansible that a test had to keep honest.
#
# `cp -f`, not `-n`: these are ours, not the operator's, so an upgrade
# that restyles the portal should restyle it. Both are small enough
# that rewriting them on every start costs nothing worth measuring.
mkdir -p /data/hotspot/brand
cp -f /usr/src/app/src/anthias_server/app/branding/wifi-connect-brand.css \
    /data/hotspot/brand/brand.css
cp -f /usr/src/app/src/anthias_server/app/static/img/logo-full-splash.svg \
    /data/hotspot/brand/logo.svg

echo "Running migration..."

# The following block ensures that the migration is transactional and that the
# database is not left in an inconsistent state if the migration fails.

if [ -f /data/.anthias/anthias.db ]; then
    python -m anthias_server.manage dbbackup --noinput --clean && \
        python -m anthias_server.manage migrate --fake-initial --noinput || \
        python -m anthias_server.manage dbrestore --noinput
else
    python -m anthias_server.manage migrate && \
        python -m anthias_server.manage dbbackup --noinput --clean
fi

UVICORN_BIND_HOST="${LISTEN:-0.0.0.0}"
UVICORN_BIND_PORT="${PORT:-8080}"

# Nothing upstream of uvicorn bounds how many connections a single
# client may open at once, and an Anthias box is routinely reachable
# from the whole office LAN. A vulnerability scanner opening a few
# hundred sockets in one second is enough to walk the container's file
# descriptor table to its ceiling, and the failure is not a tidy one:
# SQLite cannot open the database, every request 500s, and the 500
# handler then cannot open 500.html either. Measured on the testbed
# against a Pi 2, where the ceiling is reached at ~1005 concurrent
# connections, the server container dies outright and is restarted by
# the `restart: always` policy.
#
# Two independent guards, because neither is sufficient alone.
#
#   * Raise the soft descriptor limit. The container ships the stock
#     1024/524288 soft/hard pair, and a process may raise its own soft
#     limit up to the hard one without any privilege — so this needs no
#     `ulimits:` key in compose, and works the same under balena, whose
#     supervisor renders its own compose file. 8192 is comfortably
#     above the worst burst measured (~780 descriptors in flight on a
#     Pi 5) while staying far below the hard limit.
#   * Bound concurrency. The descriptor raise alone would only move the
#     ceiling: the real problem is that every accepted connection runs
#     the full Django request path, including a database connect.
#     `--limit-concurrency` makes uvicorn answer 503 immediately once
#     the in-flight count is reached, which costs no database handle
#     and no template render, so a burst sheds cheaply instead of
#     taking the box down.
#
# The default of 256 is roughly six times the ~39 sockets a device
# holds while idle with the viewer polling, so ordinary operator
# traffic cannot reach it. It is overridable for anyone fronting a
# device with something that legitimately fans out harder.
ulimit -n 8192 2>/dev/null || true
UVICORN_LIMIT_CONCURRENCY="${ANTHIAS_LIMIT_CONCURRENCY:-256}"

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
    # uvicorn's --reload watches *.py only by default. Add Django
    # templates and built CSS so a template / SCSS edit on the host
    # propagates to the running worker without a manual restart.
    exec uvicorn anthias_server.django_project.asgi:application \
        --host "$UVICORN_BIND_HOST" \
        --port "$UVICORN_BIND_PORT" \
        --timeout-keep-alive 30 \
        --limit-concurrency "$UVICORN_LIMIT_CONCURRENCY" \
        --reload \
        --reload-dir /usr/src/app \
        --reload-include "*.html" \
        --reload-include "*.css" \
        "${UVICORN_PROXY_ARGS[@]}"
else
    # collectstatic ran at image build time (docker/Dockerfile.server.j2)
    # — STATIC_ROOT is baked into the read-only image layer. No runtime
    # invocation needed.
    echo "Starting uvicorn..."
    exec uvicorn anthias_server.django_project.asgi:application \
        --host "$UVICORN_BIND_HOST" \
        --port "$UVICORN_BIND_PORT" \
        --timeout-keep-alive 30 \
        --limit-concurrency "$UVICORN_LIMIT_CONCURRENCY" \
        "${UVICORN_PROXY_ARGS[@]}"
fi
