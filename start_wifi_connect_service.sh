#!/bin/bash

if [[ -f ./docker-compose.yml ]]; then
    docker compose up -d anthias-wifi-connect
fi
