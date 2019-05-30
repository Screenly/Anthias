#!/usr/bin/env bash

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILE="$DIR/../docker-compose.dev.yml"
docker-compose -f "$FILE" "$@"
