#!/bin/bash
#
# In-container companion to bin/migrate_legacy_paths.sh.
#
# After the rebrand, container code reads from /data/.anthias and
# /data/anthias_assets. Defensively expose the legacy paths as symlinks
# in case:
#   - The user is running an older docker-compose.yml that still mounts
#     `~/.screenly:/data/.screenly` while the host data has already been
#     migrated (so the bind mount lands on a symlink, which docker
#     dereferences).
#   - DB rows or external integrations stored absolute container paths
#     beginning with /data/.screenly or /data/screenly_assets.
#
# Idempotent. Skips creation if the legacy path already exists (e.g. a
# bind mount took precedence).

set -euo pipefail

create_symlink_if_absent() {
    local link="$1"
    local target="$2"

    if [ -e "${target}" ] && [ ! -e "${link}" ] && [ ! -L "${link}" ]; then
        ln -s "${target}" "${link}"
    fi
}

create_symlink_if_absent /data/.screenly        /data/.anthias
create_symlink_if_absent /data/screenly_assets  /data/anthias_assets
