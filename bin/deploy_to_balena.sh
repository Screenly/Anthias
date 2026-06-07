#!/bin/bash

set -euo pipefail

print_help() {
    echo "Usage: deploy_to_balena.sh [options]"
    echo "Options:"
    echo "  -h, --help            show this help message and exit"
    echo "  -b, --board BOARD     specify the board to build for (pi2, pi3, pi3-64, pi4-64, pi5, x86, rockpi4)"
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

            if [[ $BOARD =~ ^(pi2|pi3|pi3-64|pi4-64|pi5|x86|rockpi4)$ ]]; then
                echo "Building for $BOARD"
            else
                echo "Invalid board $BOARD"
                print_help
                exit 1
            fi

            # The rockpi4 fleet has no board-specific image build; it
            # runs the generic arm64 containers. Rewrite BOARD so the
            # compose render pins <short-hash>-arm64 image tags (the
            # fleet itself comes from --fleet).
            if [[ $BOARD == rockpi4 ]]; then
                export BOARD=arm64
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

# Single source of truth for the release version: pyproject's
# [project].version (CalVer YYYY.M.MICRO). render_balena_yml.sh
# normalizes it to balena-compliant semver before stamping balena.yml.
# `|| true` keeps a failed grep (line missing/reformatted) from aborting
# under pipefail with no message; the explicit check below fails clearly.
RELEASE_VERSION="$(
    grep -m1 '^version = ' pyproject.toml | sed -E 's/^version = "(.*)"$/\1/' || true
)"
if [[ -z "$RELEASE_VERSION" ]]; then
    echo "Could not read [project].version from pyproject.toml" >&2
    exit 1
fi

if [[ -z "${SHM_SIZE+x}" ]]; then
    echo "Using default /dev/shm size of $DEFAULT_SHM_SIZE for the viewer service"
    export SHM_SIZE=$DEFAULT_SHM_SIZE
fi

function prepare_balena_file() {
    bin/render_balena_yml.sh balena-deploy "$RELEASE_VERSION"
    cat docker-compose.balena.yml.tmpl | \
    envsubst > balena-deploy/docker-compose.yml

    # Pi 5, x86 and non-Pi arm64 SBCs (the rockpi4 fleet's images)
    # don't expose /dev/vchiq; strip the bind mount.
    if [[ $BOARD =~ ^(pi5|x86|arm64)$ ]]; then
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
