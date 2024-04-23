#!/bin/bash

function install_nodejs {
    wget https://nodejs.org/dist/v20.12.2/node-v20.12.2-linux-x64.tar.xz
    tar -xf node-v20.12.2-linux-x64.tar.xz -C /usr/local --strip-components=1
}

# Check if DEVELOPMENT_MODE is set to 1. If yes, print something.
if [[ -n "${DEV_MODE}" ]] && [[ "${DEV_MODE}" -ne 0 ]]; then
    apt-get install -y --no-install-recommends wget
    install_nodejs
fi
