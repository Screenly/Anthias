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
        # add an option for specifying the fleet name
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
        # add argument for the short hash. default to 'latest' if not specified
        -s|--short-hash)
            export GIT_SHORT_HASH="$2"
            shift
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

prepare_balena_file

if ! balena whoami; then
    balena login -t $TOKEN
fi

balena push --source ./balena-deploy $FLEET
