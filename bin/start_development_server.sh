#!/bin/bash

set -euo pipefail

COMPOSE_ARGS=(
    '-f' 'docker-compose.dev.yml'
)

function initialize_python_environment() {
    ./bin/install_poetry.sh

    # Add `pyenv` to the load path.
    export PYENV_ROOT="$HOME/.pyenv"
    [[ -d $PYENV_ROOT/bin ]] && \
        export PATH="$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init -)"

    # Add `poetry to the load path.
    export PATH="$HOME/.local/bin:$PATH"

    poetry install --only=docker-image-builder
}

function generate_dockerfiles() {
    poetry run python tools/image_builder \
        --environment=development \
        --dockerfiles-only \
        --disable-cache-mounts
}

initialize_python_environment
generate_dockerfiles
docker compose "${COMPOSE_ARGS[@]}" down
docker compose "${COMPOSE_ARGS[@]}" up -d --build
