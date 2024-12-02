#!/bin/bash

set -euo pipefail

COMPOSE_ARGS=(
    '-f' 'docker-compose.dev.yml'
)

bin/generate_dev_mode_dockerfiles.sh

docker compose "${COMPOSE_ARGS[@]}" down
docker compose "${COMPOSE_ARGS[@]}" up -d --build
