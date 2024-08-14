#!/bin/bash

set -euo pipefail

function print_usage() {
  echo "Usage: $0 /path/to/certificate.crt"
}

if [ "$#" -ne 1 ]; then
  print_usage
  exit 1
fi

CERTIFICATE_PATH=$1

function main() {
    if [ ! -f $CERTIFICATE_PATH ]; then
        echo "Certificate file not found: $CERTIFICATE_PATH"
        exit 1
    fi

    CERTIFICATE_DIR='/usr/local/share/ca-certificates/custom'
    CONTAINERS=(anthias-server anthias-viewer)
    CERTIFICATE_FILENAME=$(basename $CERTIFICATE_PATH)

    cd $HOME/screenly

    for CONTAINER in "${CONTAINERS[@]}"; do
        docker compose exec -it $CONTAINER mkdir -p $CERTIFICATE_DIR
        docker compose cp $CERTIFICATE_PATH $CONTAINER:$CERTIFICATE_DIR
        docker compose exec -it $CONTAINER update-ca-certificates

        if [ "$CONTAINER" == "anthias-viewer" ]; then
            echo "Running certutil for $CONTAINER..."
            docker compose exec -it $CONTAINER \
                certutil -A -n "My CA Certificate" -t "C,C,C" \
                -i $CERTIFICATE_DIR/$CERTIFICATE_FILENAME \
                -d "/data/.pki/nssdb"
        fi
    done
}

main
