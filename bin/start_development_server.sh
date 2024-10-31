#!/bin/bash

set -euo pipefail

export MODE=${MODE:='partial'}

if [[ ! "$MODE" =~ ^(full|partial)$ ]]; then
    echo "MODE must be either 'full' or 'partial'"
    exit 1
fi


function generate_dockerfiles() {
    ENVIRONMENT=development \
    DOCKERFILES_ONLY=1 \
    DISABLE_CACHE_MOUNTS=1 \
    ./bin/build_containers.sh
}

generate_dockerfiles

if [[ "$MODE" == 'full' ]]; then
    docker compose down || true
    ENVIRONMENT=development ./bin/upgrade_containers.sh
else
    COMPOSE_ARGS=(
        '-f' 'docker-compose.dev.yml'
    )
    docker compose "${COMPOSE_ARGS[@]}" down
    docker compose "${COMPOSE_ARGS[@]}" up -d --build
fi

