#!/bin/bash

set -euo pipefail

print_help() {
    echo "Usage: deploy_to_balena.sh [options]"
    echo "Options:"
    echo "  -h, --help            show this help message and exit"
    echo "  -b, --board BOARD     specify the board to build for (pi1, pi2, pi3, pi4, pi5, x86)"
    echo "  -f, --fleet FLEET     specify the fleet name to deploy to"
    echo "  -s, --short-hash HASH specify the short hash to use for the image tag"
    echo "  -d, --dev             run in dev mode"
    echo "  --shm-size SIZE       specify the size of the /dev/shm partition, e.g. 256mb, 65536kb, 1gb"
}

while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        -h|--help)
            print_help
            exit 0
            ;;
        -b|--board)
            export BOARD="$2"

            if [[ $BOARD =~ ^(pi1|pi2|pi3|pi4|pi5|x86)$ ]]; then
                echo "Building for $BOARD"
            else
                echo "Invalid board $BOARD"
                print_help
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
        -s|--short-hash)
            export GIT_SHORT_HASH="$2"
            shift
            shift
            ;;
        -d|--dev)
            export DEV_MODE=1
            shift
            ;;
        --shm-size)
            export SHM_SIZE="$2"
            shift
            shift
            ;;
        *)
            echo "Unknown option $key"
            print_help
            exit 1
            ;;
    esac
done

if [[ -z "${BOARD+x}" ]]; then
    echo "Please specify a board with --board"
    print_help
    exit 1
fi

if [[ -z "${FLEET+x}" ]]; then
    echo "Please specify the fleet name with --fleet"
    print_help
    exit 1
fi

export GIT_SHORT_HASH=${GIT_SHORT_HASH:-latest}
export DEFAULT_SHM_SIZE='256mb'

if [[ -z "${SHM_SIZE+x}" ]]; then
    echo "Using default /dev/shm size of $DEFAULT_SHM_SIZE for the viewer service"
    export SHM_SIZE=$DEFAULT_SHM_SIZE
fi

function prepare_balena_file() {
    if [[ "$BOARD" == "pi4" ]]; then
        export BOARD="pi4-64"
    fi

    mkdir -p balena-deploy
    cp balena.yml balena-deploy/
    cat docker-compose.balena.yml.tmpl | \
    envsubst > balena-deploy/docker-compose.yml

    if [ "$BOARD" == "pi5" ] || [ "$BOARD" == "x86" ]; then
        sed -i '/devices:/ {N; /\n.*\/dev\/vchiq:\/dev\/vchiq/d}' \
            balena-deploy/docker-compose.yml
    fi
}

if ! balena whoami; then
    echo "Please login to Balena with `balena login` command, then run this script again."
    exit 0
fi

if [[ -z "${DEV_MODE+x}" ]]; then
    echo "Running in production mode..."

    prepare_balena_file

    balena push --source ./balena-deploy $FLEET
else
    echo "Running in dev mode..."

    ENVIRONMENT="production" \
    BUILD_TARGET="$BOARD" \
        bin/generate_dev_mode_dockerfiles.sh

    cat docker-compose.balena.dev.yml.tmpl | \
        envsubst > docker-compose.yml

    if [[ $BOARD =~ ^(pi5|x86)$ ]]; then
        sed -i '/devices:/ {N; /\n.*\/dev\/vchiq:\/dev\/vchiq/d}' \
            docker-compose.yml
    fi

    balena push $FLEET
fi
