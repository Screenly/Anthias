#!/bin/bash

set -euo pipefail


function generate_dockerfiles() {
    ENVIRONMENT=development \
    DOCKERFILES_ONLY=1 \
    DISABLE_CACHE_MOUNTS=1 \
    ./bin/build_containers.sh
}

function main() {
    local COMPOSE_ARGS=(
        '-f' 'docker-compose.dev.yml'
    )

    generate_dockerfiles
    docker compose "${COMPOSE_ARGS[@]}" down
    docker compose "${COMPOSE_ARGS[@]}" up -d --build
}

main
