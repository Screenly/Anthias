#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euo pipefail

GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
GIT_SHORT_HASH=$(git rev-parse --short HEAD)
GIT_HASH=$(git rev-parse HEAD)


# Make sure balena cli is installed
if ! which balena > /dev/null; then
    echo 'Balena CLI not found. Please install and run again.'
    echo 'Installation instructions can be found here:'
    echo 'https://github.com/balena-io/balena-cli/blob/master/INSTALL.md'
    exit 1
fi


read -p "What is the target device? (pi1-pi4)? " -r DEVICE_TYPE

echo 'Here are your Balena apps:'
balena apps
echo

read -p "Enter the app name of the app you want to deploy to (needs to be SLUG): " -r APP_NAME

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

echo "Setting variables..."
balena env add BALENA_HOST_CONFIG_gpu_mem 128 --application "$APP_NAME"
balena env add BALENA_HOST_CONFIG_framebuffer_depth 32 --application "$APP_NAME"
balena env add BALENA_HOST_CONFIG_framebuffer_ignore_alpha 1 --application "$APP_NAME"

if [ "$DEVICE_TYPE" = "pi4" ]; then
    balena env add BALENA_HOST_CONFIG_dtparam "\"i2c_arm=on\",\"spi=on\",\"audio=on\",\"vc4-kms-v3d\"" --application "$APP_NAME"
fi

balena deploy "$APP_NAME"
