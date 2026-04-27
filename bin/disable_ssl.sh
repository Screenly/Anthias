#!/bin/bash -e

# Disable HTTPS on Anthias: stop + remove the Caddy sidecar that
# bin/enable_ssl.sh installed, drop the compose override, and bring
# anthias-server back up directly on port 80. The certificate +
# Caddyfile under ~/.anthias/ are left in place so SSL can be
# re-enabled with bin/enable_ssl.sh without regenerating them.

COMPOSE_DIR="${HOME}/anthias"
OVERRIDE_FILE="${COMPOSE_DIR}/docker-compose.ssl.override.yml"

if [[ -f "$OVERRIDE_FILE" ]]; then
    sudo -E docker compose \
        -f "$COMPOSE_DIR/docker-compose.yml" \
        -f "$OVERRIDE_FILE" \
        rm -sf anthias-caddy >/dev/null 2>&1 || true
    rm -f "$OVERRIDE_FILE"
    echo "Removed $OVERRIDE_FILE and the anthias-caddy container."
fi

sudo -E docker compose \
    -f "$COMPOSE_DIR/docker-compose.yml" \
    up -d --force-recreate anthias-server

echo
echo "SSL disabled. Anthias is now reachable at http://<your IP> (port 80)."
echo
echo "Caddy's local CA + issued certs are kept in the docker volume"
echo "  anthias_anthias-caddy-data"
echo "so re-enabling SSL with bin/enable_ssl.sh reuses the same CA"
echo "(browsers that already trusted it stay trusted). To wipe the CA"
echo "and start over, run:"
echo "  docker volume rm anthias_anthias-caddy-data anthias_anthias-caddy-config"
