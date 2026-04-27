#!/bin/bash -e

# Enable HTTPS on Anthias by adding a Caddy sidecar that terminates TLS
# in front of anthias-server. Three modes:
#
#   bin/enable_ssl.sh
#       Default. Caddy issues a cert from its built-in local CA. Good
#       for IP-based access on a LAN — browsers will warn (the CA is
#       self-signed) but no openssl/cert-management is needed.
#
#   bin/enable_ssl.sh --domain example.com [--email me@example.com] [--staging]
#       Caddy auto-issues + renews from Let's Encrypt. Requires the
#       domain to resolve to this host and port 80 to be reachable
#       (HTTP-01 challenge). --staging uses the Let's Encrypt staging
#       endpoint for testing.
#
#   bin/enable_ssl.sh --cert C.pem --key K.pem [--domain example.com]
#       Bring your own cert. Anthias copies the cert + key under
#       ~/.anthias/ssl/ and Caddy serves them as-is (no ACME).
#
# A compose override (docker-compose.ssl.override.yml) is generated to:
#   * stand up anthias-caddy on host ports 80 + 443 (Caddy redirects 80
#     to 443 and reverse-proxies HTTPS to anthias-server:8080),
#   * remove anthias-server's external port mapping so all external
#     traffic flows through Caddy (and the IP allowlists in
#     views_files.py see the original LAN client IP via X-Forwarded-For),
#   * tell uvicorn to honour Caddy's X-Forwarded-* headers.
#
# Inter-container traffic (viewer / webview / celery → anthias-server)
# stays on plain HTTP over the Docker network — Caddy is only on the
# external path.
#
# Disable later with: bin/disable_ssl.sh
#
# Note: bin/add_certificate.sh is the *outbound* counterpart — it
# installs a CA into the trust stores of anthias-server and
# anthias-viewer so Anthias can verify private HTTPS asset hosts. The
# two scripts are independent.

DOMAIN=""
EMAIL=""
STAGING=0
USER_CERT=""
USER_KEY=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain)
            DOMAIN="$2"
            shift 2
            ;;
        --email)
            EMAIL="$2"
            shift 2
            ;;
        --staging)
            STAGING=1
            shift
            ;;
        --cert)
            USER_CERT="$2"
            shift 2
            ;;
        --key)
            USER_KEY="$2"
            shift 2
            ;;
        -h|--help)
            sed -n '3,33p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

if [[ -n "$USER_CERT" && -z "$USER_KEY" ]] \
   || [[ -z "$USER_CERT" && -n "$USER_KEY" ]]; then
    echo "--cert and --key must be supplied together." >&2
    exit 2
fi

ANTHIAS_DIR="${HOME}/.anthias"
SSL_DIR="${ANTHIAS_DIR}/ssl"
CADDYFILE="${ANTHIAS_DIR}/Caddyfile"
COMPOSE_DIR="${HOME}/anthias"
OVERRIDE_FILE="${COMPOSE_DIR}/docker-compose.ssl.override.yml"

# Decide which TLS strategy Caddy will use.
SITE_ADDRESS=""
TLS_DIRECTIVE=""
EXTRA_GLOBAL=""
MOUNT_SSL_DIR=0
NEEDS_REDIRECT_BLOCK=0

if [[ -n "$USER_CERT" ]]; then
    [[ -f "$USER_CERT" ]] || { echo "Cert not found: $USER_CERT" >&2; exit 1; }
    [[ -f "$USER_KEY"  ]] || { echo "Key not found: $USER_KEY"   >&2; exit 1; }
    echo "Installing user-supplied certificate from $USER_CERT..."
    mkdir -p "$SSL_DIR"
    chmod 700 "$SSL_DIR"
    cp "$USER_CERT" "$SSL_DIR/cert.pem"
    cp "$USER_KEY"  "$SSL_DIR/key.pem"
    chmod 600 "$SSL_DIR/key.pem"

    SITE_ADDRESS="${DOMAIN:-:443}"
    TLS_DIRECTIVE="tls /etc/anthias/ssl/cert.pem /etc/anthias/ssl/key.pem"
    # Disable Caddy's ACME path; we're serving the supplied cert.
    EXTRA_GLOBAL="    auto_https off"
    MOUNT_SSL_DIR=1
    [[ -z "$DOMAIN" ]] && NEEDS_REDIRECT_BLOCK=1
elif [[ -n "$DOMAIN" ]]; then
    echo "Caddy will auto-issue a Let's Encrypt cert for $DOMAIN."
    SITE_ADDRESS="$DOMAIN"
    # Empty TLS directive — Caddy auto-manages via auto_https when the
    # site address is a hostname. Caddy also auto-creates a :80→:443
    # redirect for hostname sites, so no explicit redir block needed.
    EXTRA_GLOBAL=""
    [[ -n "$EMAIL" ]] && EXTRA_GLOBAL+="    email $EMAIL"
    if [[ "$STAGING" == "1" ]]; then
        [[ -n "$EXTRA_GLOBAL" ]] && EXTRA_GLOBAL+=$'\n'
        EXTRA_GLOBAL+="    acme_ca https://acme-staging-v02.api.letsencrypt.org/directory"
    fi
else
    echo "Caddy will issue a cert from its internal local CA (browsers will warn)."
    SITE_ADDRESS=":443"
    # `on_demand` lets Caddy issue per-SNI certs lazily so the same
    # listener can serve any IP / hostname the device is reached on.
    # Safe without an `ask` endpoint because it's the local CA, not a
    # public one — there's no rate-limited issuer to abuse.
    TLS_DIRECTIVE="tls internal {
        on_demand
    }"
    NEEDS_REDIRECT_BLOCK=1
fi

# Build the Caddyfile.
{
    echo "{"
    echo "    admin off"
    [[ -n "$EXTRA_GLOBAL" ]] && echo "$EXTRA_GLOBAL"
    echo "}"
    echo
    if [[ "$NEEDS_REDIRECT_BLOCK" == "1" ]]; then
        cat <<'REDIR'
:80 {
    redir https://{host}{uri} permanent
}

REDIR
    fi
    echo "$SITE_ADDRESS {"
    [[ -n "$TLS_DIRECTIVE" ]] && echo "    $TLS_DIRECTIVE"
    cat <<'BODY'
    request_body {
        max_size 0
    }
    reverse_proxy anthias-server:8080
}
BODY
} > "$CADDYFILE"

# Build the compose override.
# `!override []` empties anthias-server's `ports:` list, so the host's
# port 80 mapping is removed and all external traffic must enter
# through Caddy. Inter-container traffic still reaches
# anthias-server:8080 over the Docker network.
#
# FORWARDED_ALLOW_IPS=* on anthias-server is only safe BECAUSE this
# override drops the external port mapping above — only Caddy and
# other Docker-network containers can reach anthias-server. If you
# re-add a host:port mapping for anthias-server, tighten this to the
# Caddy container's IP/CIDR or back to unset, otherwise any client
# could spoof X-Forwarded-* through to uvicorn.
{
    cat <<EOF
# Generated by bin/enable_ssl.sh — delete this file (and re-run
# \`docker compose up -d\`) or run bin/disable_ssl.sh to disable SSL.
services:
  anthias-server:
    ports: !override []
    environment:
      - FORWARDED_ALLOW_IPS=*

  anthias-caddy:
    image: caddy:2-alpine
    restart: always
    ports:
      - 80:80
      - 443:443
    depends_on:
      - anthias-server
    volumes:
      - ${CADDYFILE}:/etc/caddy/Caddyfile:ro
EOF
    if [[ "$MOUNT_SSL_DIR" == "1" ]]; then
        echo "      - ${SSL_DIR}:/etc/anthias/ssl:ro"
    fi
    cat <<'EOF'
      - anthias-caddy-data:/data
      - anthias-caddy-config:/config

volumes:
  anthias-caddy-data:
  anthias-caddy-config:
EOF
} > "$OVERRIDE_FILE"

echo "Wrote compose override: $OVERRIDE_FILE"
echo "Wrote Caddyfile:        $CADDYFILE"
echo "Restarting compose with Caddy + TLS enabled..."

sudo -E docker compose \
    -f "$COMPOSE_DIR/docker-compose.yml" \
    -f "$OVERRIDE_FILE" \
    up -d --force-recreate anthias-server anthias-caddy

echo
if [[ -n "$DOMAIN" ]]; then
    echo "SSL enabled. Anthias is now reachable at https://$DOMAIN/"
else
    echo "SSL enabled. Anthias is now reachable at https://<your IP>/"
fi
echo "HTTP on port 80 redirects to HTTPS."
if [[ -z "$USER_CERT" && -z "$DOMAIN" ]]; then
    echo "(Caddy local CA — browsers will warn on first visit.)"
fi
echo
echo "If you have a firewall, open TCP/443 — e.g. \`sudo ufw allow 443/tcp\`."
