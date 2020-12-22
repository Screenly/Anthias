#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euo pipefail

GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
GIT_SHORT_HASH=$(git rev-parse --short HEAD)
GIT_HASH=$(git rev-parse HEAD)

read -p "What is the target device? (pi1-pi4)? " -r DEVICE_TYPE

mkdir -p .balena
cat <<EOF > .balena/balena.yml
build-variables:
  global:
    - GIT_HASH=$GIT_HASH
    - GIT_SHORT_HASH=$GIT_SHORT_HASH
    - GIT_BRANCH=$GIT_BRANCH
  services:
      srly-ose-viewer:
        - DEVICE_TYPE=$DEVICE_TYPE
EOF
