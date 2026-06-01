#!/bin/bash
#
# In-container companion to bin/migrate_legacy_paths.sh.
#
# bin/migrate_legacy_paths.sh renames the pre-rebrand host paths
# (~/.screenly -> ~/.anthias, ~/screenly_assets -> ~/anthias_assets,
# screenly.db -> anthias.db). It runs from bin/install.sh and
# bin/upgrade_containers.sh — i.e. only on the apt/ansible host install.
# On balena there is no host install step, so the persistent /data volume
# of a pre-rebrand device still holds /data/.screenly + /data/screenly_assets
# and the new container code, which reads /data/.anthias + /data/anthias_assets,
# would start against an EMPTY database (issue: upgraded legacy devices lose
# their playlist). This script performs the equivalent migration in-container
# so balena devices are covered too.
#
# Idempotent and safe to run on every container start.

set -euo pipefail

ANTHIAS_DIR=/data/.anthias
SCREENLY_DIR=/data/.screenly
ANTHIAS_ASSETS=/data/anthias_assets
SCREENLY_ASSETS=/data/screenly_assets

log() {
    echo "[migrate_in_container_paths] $*"
}

# 1. Adopt legacy data into the new paths. A rename within /data is a
#    cheap same-filesystem move. Only fires when the legacy dir is real
#    (not already a back-compat symlink) and the new path doesn't exist
#    yet — so it is a no-op on clean installs and on already-migrated
#    devices.
adopt_legacy_dir() {
    local legacy="$1"
    local new="$2"
    if [ -d "${legacy}" ] && [ ! -L "${legacy}" ] && [ ! -e "${new}" ]; then
        log "Adopting ${legacy} -> ${new}"
        mv "${legacy}" "${new}"
    fi
}

adopt_legacy_dir "${SCREENLY_DIR}" "${ANTHIAS_DIR}"
adopt_legacy_dir "${SCREENLY_ASSETS}" "${ANTHIAS_ASSETS}"

# 1b. Expose the legacy db / conf under the new names inside the config
#     dir (mirrors migrate_legacy_paths.sh's screenly.db -> anthias.db).
if [ -e "${ANTHIAS_DIR}/screenly.db" ] && [ ! -e "${ANTHIAS_DIR}/anthias.db" ]; then
    ln -s screenly.db "${ANTHIAS_DIR}/anthias.db"
fi
if [ -e "${ANTHIAS_DIR}/screenly.conf" ] && [ ! -e "${ANTHIAS_DIR}/anthias.conf" ]; then
    ln -s screenly.conf "${ANTHIAS_DIR}/anthias.conf"
fi

# 1c. Recover the already-broken state. A device that booted a
#     post-rebrand release before this fix shipped created an EMPTY
#     /data/.anthias/anthias.db (a fresh `migrate`) while its real data
#     stayed behind in /data/.screenly/screenly.db. adopt_legacy_dir
#     can't help there (the new dir already exists), so detect an empty
#     anthias.db sitting next to a populated legacy screenly.db and adopt
#     the latter. The empty db is preserved as a timestamped .emptybak.
if [ -f "${ANTHIAS_DIR}/anthias.db" ] && [ -f "${SCREENLY_DIR}/screenly.db" ] \
    && [ ! -L "${ANTHIAS_DIR}/anthias.db" ]; then
    python3 - "${ANTHIAS_DIR}/anthias.db" "${SCREENLY_DIR}/screenly.db" <<'PY' || true
import shutil
import sqlite3
import sys
import time

anthias_db, screenly_db = sys.argv[1], sys.argv[2]


def asset_count(path):
    try:
        conn = sqlite3.connect(path)
        return conn.execute('SELECT COUNT(*) FROM assets').fetchone()[0]
    except Exception:
        return -1


if asset_count(anthias_db) == 0 and asset_count(screenly_db) > 0:
    shutil.copy2(anthias_db, f'{anthias_db}.emptybak.{int(time.time())}')
    shutil.copy2(screenly_db, anthias_db)
    print('[migrate_in_container_paths] recovered playlist from legacy '
          'screenly.db (empty anthias.db replaced)')
PY
fi

# 2. Reverse-compat symlinks: once data lives under the new paths, expose
#    the legacy names too, in case an older docker-compose.yml still binds
#    `~/.screenly:/data/.screenly` or a DB row / integration stored an
#    absolute /data/.screenly or /data/screenly_assets path. No-op if the
#    legacy path already exists (e.g. a bind mount took precedence).
create_symlink_if_absent() {
    local link="$1"
    local target="$2"

    if [ -e "${target}" ] && [ ! -e "${link}" ] && [ ! -L "${link}" ]; then
        ln -s "${target}" "${link}"
    fi
}

create_symlink_if_absent "${SCREENLY_DIR}" "${ANTHIAS_DIR}"
create_symlink_if_absent "${SCREENLY_ASSETS}" "${ANTHIAS_ASSETS}"
