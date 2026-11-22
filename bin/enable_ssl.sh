#!/bin/bash -e

# Choose the certificate Anthias' web interface serves over HTTPS.
#
# HTTPS is always on: the anthias-caddy container owns ports 80 and 443
# on every install, and by default serves the screen's own self-signed
# certificate (browsers warn). This script switches it to something a
# browser trusts, ending in exactly the same state as Settings ->
# HTTPS certificate in the web UI:
#
#   bin/enable_ssl.sh --cert C.pem --key K.pem [--chain CHAIN.pem]
#       Serve your own certificate. It is checked first: the key must
#       match, the certificate must not be expired, and a chain must be
#       in order (leaf first). C.pem may already contain the chain.
#
#   bin/enable_ssl.sh --domain example.com [--email me@example.com] [--staging]
#       Caddy obtains and renews a Let's Encrypt certificate. The domain
#       must resolve to this screen and port 80 or 443 must be reachable
#       from the internet. --staging uses Let's Encrypt's staging CA.
#
#   bin/enable_ssl.sh
#       Go back to the screen's own certificate (same as
#       bin/disable_ssl.sh).
#
# None of this changes the screen's own (node) certificate, which other
# Anthias screens use to recognise this one.
#
# Plain HTTP on port 80 redirects the web UI to HTTPS. /api/* keeps
# answering over plain HTTP for one more release so existing scripts
# can move to https:// first.

DOMAIN=""
EMAIL=""
STAGING=0
USER_CERT=""
USER_KEY=""
USER_CHAIN=""

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
        --chain)
            USER_CHAIN="$2"
            shift 2
            ;;
        -h|--help)
            sed -n '3,30p' "$0"
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
if [[ -n "$USER_CERT" && -n "$DOMAIN" ]]; then
    echo "Use either --cert/--key or --domain, not both." >&2
    exit 2
fi

COMPOSE_DIR="${HOME}/anthias"
ANTHIAS_DIR="${HOME}/.anthias"
COMPOSE=(sudo -E docker compose -f "${COMPOSE_DIR}/docker-compose.yml")

function web_tls() {
    "${COMPOSE[@]}" exec -T anthias-server \
        python -m anthias_server.manage web_tls "$@"
}

if [[ -n "$USER_CERT" ]]; then
    for f in "$USER_CERT" "$USER_KEY" ${USER_CHAIN:+"$USER_CHAIN"}; do
        [[ -f "$f" ]] || { echo "Not found: $f" >&2; exit 1; }
    done
    # Hand the files over through the config directory, which
    # anthias-server mounts at /data/.anthias, and remove them again
    # whatever happens. The validated copy is stored under
    # ~/.anthias/ssl/web/.
    INCOMING="$(mktemp -d "${ANTHIAS_DIR}/ssl-import.XXXXXX")"
    trap 'rm -rf "$INCOMING"' EXIT
    chmod 700 "$INCOMING"
    cp "$USER_CERT" "$INCOMING/cert.pem"
    cp "$USER_KEY" "$INCOMING/key.pem"
    ARGS=(custom
        --cert "/data/.anthias/$(basename "$INCOMING")/cert.pem"
        --key "/data/.anthias/$(basename "$INCOMING")/key.pem")
    if [[ -n "$USER_CHAIN" ]]; then
        cp "$USER_CHAIN" "$INCOMING/chain.pem"
        ARGS+=(--chain "/data/.anthias/$(basename "$INCOMING")/chain.pem")
    fi
    web_tls "${ARGS[@]}"
    echo "Anthias now serves your certificate at https://<your hostname>/"
elif [[ -n "$DOMAIN" ]]; then
    ARGS=(acme --domain "$DOMAIN")
    [[ -n "$EMAIL" ]] && ARGS+=(--email "$EMAIL")
    [[ "$STAGING" == "1" ]] && ARGS+=(--staging)
    web_tls "${ARGS[@]}"
    echo "Caddy is requesting a Let's Encrypt certificate for $DOMAIN."
    echo "Anthias will be reachable at https://$DOMAIN/ once it is issued."
    echo "If you have a firewall, open TCP/80 and TCP/443."
else
    web_tls default
    echo "Anthias serves its own certificate at https://<your IP>/"
    echo "(browsers will warn until you install a trusted certificate)."
fi
