#!/bin/bash

# Check if DEVELOPMENT_MODE is set to 1. If yes, print something.
if [[ -n "${DEV_MODE}" ]] && [[ "${DEV_MODE}" -ne 0 ]]; then
    echo "Development mode is enabled. Installing Node.js..."
    apt-get install -y nodejs npm
fi
