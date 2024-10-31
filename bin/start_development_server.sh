#!/bin/bash

set -euo pipefail

COMPOSE_ARGS=(
    '-f' 'docker-compose.dev.yml'
)

function generate_dockerfiles() {
    ENVIRONMENT=development \
    DOCKERFILES_ONLY=1 \
    DISABLE_CACHE_MOUNTS=1 \
    ./bin/build_containers.sh
}

generate_dockerfiles
docker compose "${COMPOSE_ARGS[@]}" down
docker compose "${COMPOSE_ARGS[@]}" up -d --build
