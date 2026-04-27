#!/bin/bash -e

# Disable HTTPS on Anthias: remove the compose override produced by
# bin/enable_ssl.sh and bring the server back up on plain port 80.
# The certificate under ~/.anthias/ssl is left in place so it can be
# reused if SSL is re-enabled.

COMPOSE_DIR="${HOME}/anthias"
OVERRIDE_FILE="${COMPOSE_DIR}/docker-compose.ssl.override.yml"

if [[ -f "$OVERRIDE_FILE" ]]; then
    rm -f "$OVERRIDE_FILE"
    echo "Removed $OVERRIDE_FILE."
fi

sudo -E docker compose \
    -f "$COMPOSE_DIR/docker-compose.yml" \
    up -d --force-recreate anthias-server

echo
echo "SSL disabled. Anthias is now reachable at http://<your IP> (port 80)."
