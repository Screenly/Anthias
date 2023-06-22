#!/bin/bash

set -euo pipefail

while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        -b|--board)
            export BOARD="$2"

            if [[ $BOARD =~ ^(pi1|pi2|pi3|pi4)$ ]]; then
                echo "Building for $BOARD"
            else
                echo "Invalid board $BOARD"
                exit 1
            fi

            shift
            shift
            ;;
        -f|--fleet)
            export FLEET="$2"
            shift
            shift
            ;;
        -t|--token)
            export TOKEN="$2"
            shift
            shift
            ;;
        -s|--short-hash)
            export GIT_SHORT_HASH="$2"
            shift
            shift
            ;;
        # add an option whether to run in dev mode or not
        -d|--dev)
            export DEV_MODE=1
            shift
            ;;
        *)
            echo "Unknown option $key"
            exit 1
            ;;
    esac
done

if [[ -z "${BOARD+x}" ]]; then
    echo "Please specify a board with --board"
    exit 1
fi

if [[ -z "${FLEET+x}" ]]; then
    echo "Please specify the fleet name with --fleet"
    exit 1
fi

if [[ -z "${TOKEN+x}" ]]; then
    echo "Please specify a Balena token with --token"
    exit 1
fi

export GIT_SHORT_HASH=${GIT_SHORT_HASH:-latest}

function prepare_balena_file() {
    mkdir -p balena-deploy
    cp balena.yml balena-deploy/
    cat docker-compose.balena.yml.tmpl | \
    envsubst > balena-deploy/docker-compose.yml
}

if ! balena whoami; then
    balena login -t $TOKEN
fi

if [[ -z "${DEV_MODE+x}" ]]; then
    echo "Running in production mode..."

    prepare_balena_file

    balena push --source ./balena-deploy $FLEET
else
    echo "Running in dev mode..."

    DOCKERFILES_ONLY=1 DEV_MODE=1 BUILD_TARGET=${BOARD} \
        ./bin/build_containers.sh
    cat docker-compose.balena.dev.yml.tmpl | \
        envsubst > docker-compose.yml

    balena push $FLEET
fi
