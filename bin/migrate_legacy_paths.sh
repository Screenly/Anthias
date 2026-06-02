#!/bin/bash
#
# Idempotent screenly -> anthias data-path migration.
#
# HOST mode (no argument) — apt/ansible installs, operating on the user's
# home dir:
#   ~/screenly/         -> ~/anthias/         (the cloned repo)
#   ~/screenly_assets/  -> ~/anthias_assets/  (user media)
#   ~/.screenly/        -> ~/.anthias/        (config + DB + dbbackup)
#   ~/.anthias/screenly.db   -> ~/.anthias/anthias.db
#   ~/.anthias/screenly.conf -> ~/.anthias/anthias.conf
# Invoked from bin/install.sh (before clone_repo) and
# bin/upgrade_containers.sh.
#
# DATA mode (explicit base dir, e.g. `/data`) — balena, where there is no
# host install step to run this. Runs the same config / asset / DB
# migration against <base>, minus the host-only repo rename and sudoers
# cleanup. bin/migrate_in_container_paths.sh calls it as
# `migrate_legacy_paths.sh /data` from start_server.sh, so the balena
# upgrade path folds into this one canonical migration instead of a
# parallel reimplementation.
#
# After renaming, legacy locations are left as symlinks to the new
# locations so external scripts keep working and a one-version downgrade
# can still find its bind-mount sources.
#
# DATA mode additionally RECOVERS the already-broken state: a balena
# device that booted a post-rebrand release before this migration shipped
# created an empty <base>/.anthias/anthias.db (a fresh `migrate`) while
# its real data stayed in <base>/.screenly/screenly.db. We detect the
# empty db (and empty anthias_assets) and adopt the legacy data, keeping
# the empty db as a .emptybak.
#
# Safe to run multiple times. Exits 0 when nothing to do (fresh install).

set -euo pipefail

# Resolve the user's home dir without trusting $HOME (this script may be
# invoked under sudo). $USER is unset when run inside the viewer
# container (DATA mode, via migrate_in_container_paths.sh), so fall back
# to `id -un` rather than letting `set -u` abort the whole migration on
# an unbound $USER. USER_HOME is only actually used in HOST mode; in
# DATA mode it just needs to resolve without erroring.
USER_HOME="${USER_HOME:-/home/${USER:-$(id -un)}}"

# HOST mode (no arg) operates on $USER_HOME and additionally does the repo
# rename + sudoers cleanup. DATA mode (explicit base dir) does only the
# config / assets / DB migration + the balena broken-state recovery.
if [ "$#" -ge 1 ]; then
    BASE_DIR="$1"
    HOST_MODE=false
else
    BASE_DIR="${USER_HOME}"
    HOST_MODE=true
fi

OLD_REPO_DIR="${USER_HOME}/screenly"
NEW_REPO_DIR="${USER_HOME}/anthias"
OLD_ASSETS_DIR="${BASE_DIR}/screenly_assets"
NEW_ASSETS_DIR="${BASE_DIR}/anthias_assets"
OLD_CONFIG_DIR="${BASE_DIR}/.screenly"
NEW_CONFIG_DIR="${BASE_DIR}/.anthias"

log() {
    echo "[migrate_legacy_paths] $*"
}

# If we were started from inside ~/screenly and need to rename it, copy
# ourselves to /tmp and re-exec from there. Otherwise the `mv` would
# pull the running script's directory out from under us. (HOST mode only.)
self_relocate_if_needed() {
    local script_path
    script_path="$(readlink -f "$0")"

    case "${script_path}" in
        "${OLD_REPO_DIR}"/*)
            ;;
        *)
            return 0
            ;;
    esac

    if [ ! -d "${OLD_REPO_DIR}/.git" ] || [ -d "${NEW_REPO_DIR}/.git" ]; then
        return 0
    fi

    local tmp_copy
    tmp_copy="$(mktemp /tmp/migrate_legacy_paths.XXXXXX.sh)"
    cp "${script_path}" "${tmp_copy}"
    chmod +x "${tmp_copy}"
    log "Re-executing from ${tmp_copy} so the repo dir can be renamed."
    exec "${tmp_copy}" "$@"
}

migrate_repo_dir() {
    if [ -d "${OLD_REPO_DIR}/.git" ] && [ ! -e "${NEW_REPO_DIR}" ]; then
        log "Renaming ${OLD_REPO_DIR} -> ${NEW_REPO_DIR}"
        mv "${OLD_REPO_DIR}" "${NEW_REPO_DIR}"
    fi
}

migrate_config_dir() {
    if [ -d "${OLD_CONFIG_DIR}" ] && [ ! -L "${OLD_CONFIG_DIR}" ] \
        && [ ! -e "${NEW_CONFIG_DIR}" ]; then
        log "Renaming ${OLD_CONFIG_DIR} -> ${NEW_CONFIG_DIR}"
        mv "${OLD_CONFIG_DIR}" "${NEW_CONFIG_DIR}"
    fi

    if [ -f "${NEW_CONFIG_DIR}/screenly.db" ] \
        && [ ! -e "${NEW_CONFIG_DIR}/anthias.db" ]; then
        log "Renaming screenly.db -> anthias.db"
        mv "${NEW_CONFIG_DIR}/screenly.db" "${NEW_CONFIG_DIR}/anthias.db"
    fi

    if [ -f "${NEW_CONFIG_DIR}/screenly.conf" ] \
        && [ ! -e "${NEW_CONFIG_DIR}/anthias.conf" ]; then
        log "Renaming screenly.conf -> anthias.conf and rewriting paths"
        mv "${NEW_CONFIG_DIR}/screenly.conf" "${NEW_CONFIG_DIR}/anthias.conf"
        # Non-anchored, global substitutions so absolute-path values
        # (e.g. `database = /home/pi/.screenly/screenly.db`) are
        # rewritten too. Order matters: replace the longer, more
        # specific filename pattern before the shorter dir pattern.
        sed -i \
            -e 's|\.screenly/screenly\.db|.anthias/anthias.db|g' \
            -e 's|\.screenly\b|.anthias|g' \
            "${NEW_CONFIG_DIR}/anthias.conf"
    fi

    # File-level back-compat symlinks inside the migrated config dir.
    # A prior release that still mounts ~/.screenly:/data/.screenly
    # (via the dir-level symlink) will then find its expected
    # screenly.db / screenly.conf filenames inside. NOTE: the conf
    # body has been rewritten to .anthias paths above, so a downgrade
    # past this point still requires a manual conf edit; the file-level
    # symlinks only buy us file-existence checks, not conf semantics.
    if [ -f "${NEW_CONFIG_DIR}/anthias.db" ] \
        && [ ! -e "${NEW_CONFIG_DIR}/screenly.db" ]; then
        ln -s anthias.db "${NEW_CONFIG_DIR}/screenly.db"
    fi
    if [ -f "${NEW_CONFIG_DIR}/anthias.conf" ] \
        && [ ! -e "${NEW_CONFIG_DIR}/screenly.conf" ]; then
        ln -s anthias.conf "${NEW_CONFIG_DIR}/screenly.conf"
    fi
}

migrate_assets_dir() {
    if [ -d "${OLD_ASSETS_DIR}" ] && [ ! -L "${OLD_ASSETS_DIR}" ] \
        && [ ! -e "${NEW_ASSETS_DIR}" ]; then
        log "Renaming ${OLD_ASSETS_DIR} -> ${NEW_ASSETS_DIR}"
        mv "${OLD_ASSETS_DIR}" "${NEW_ASSETS_DIR}"
    fi
}

# Recover a balena device that already booted a post-rebrand release
# before this migration shipped: the rename above is a no-op there
# (NEW_CONFIG_DIR already exists, created empty), so explicitly adopt the
# legacy data the empty new paths are shadowing. DATA mode only.
recover_broken_data() {
    # DB: an empty anthias.db sitting next to a populated legacy
    # screenly.db. Keep the empty db as a timestamped .emptybak.
    if [ -f "${NEW_CONFIG_DIR}/anthias.db" ] \
        && [ ! -L "${NEW_CONFIG_DIR}/anthias.db" ] \
        && [ -f "${OLD_CONFIG_DIR}/screenly.db" ]; then
        python3 - "${NEW_CONFIG_DIR}/anthias.db" "${OLD_CONFIG_DIR}/screenly.db" <<'PY' || true
import shutil
import sqlite3
import sys
import time

new_db, legacy_db = sys.argv[1], sys.argv[2]


def asset_count(path):
    try:
        return sqlite3.connect(path).execute(
            'SELECT COUNT(*) FROM assets').fetchone()[0]
    except Exception:
        return -1


if asset_count(new_db) == 0 and asset_count(legacy_db) > 0:
    shutil.copy2(new_db, f'{new_db}.emptybak.{int(time.time())}')
    shutil.copy2(legacy_db, new_db)
    print('[migrate_legacy_paths] recovered playlist from legacy '
          'screenly.db (empty anthias.db replaced)')
PY
    fi

    # Assets: an empty anthias_assets dir next to populated legacy media.
    # The asset rows reference /…/screenly_assets paths, so exposing the
    # legacy media under the new dir keeps both the rows and any new
    # uploads resolving to one place.
    if [ -d "${NEW_ASSETS_DIR}" ] && [ ! -L "${NEW_ASSETS_DIR}" ] \
        && [ -z "$(ls -A "${NEW_ASSETS_DIR}" 2>/dev/null)" ] \
        && [ -d "${OLD_ASSETS_DIR}" ] && [ ! -L "${OLD_ASSETS_DIR}" ] \
        && [ -n "$(ls -A "${OLD_ASSETS_DIR}" 2>/dev/null)" ]; then
        log "Linking empty ${NEW_ASSETS_DIR} -> ${OLD_ASSETS_DIR} (legacy media)"
        rmdir "${NEW_ASSETS_DIR}" && ln -s "${OLD_ASSETS_DIR}" "${NEW_ASSETS_DIR}"
    fi
}

# Leave one-version-back compatibility symlinks at the old paths so:
#   - users who SSH in and reference ~/.screenly directly keep working
#   - cron jobs / rsync / backup scripts targeting old paths keep working
#   - rolling back to a release that mounts the legacy paths still finds
#     its bind-mount source
#
# These will be removed in a future release once the rename has been out
# in the field for one cycle.
create_back_compat_symlinks() {
    if [ "${HOST_MODE}" = true ] \
        && [ -d "${NEW_REPO_DIR}" ] && [ ! -e "${OLD_REPO_DIR}" ]; then
        log "Creating compat symlink ${OLD_REPO_DIR} -> ${NEW_REPO_DIR}"
        ln -s "${NEW_REPO_DIR}" "${OLD_REPO_DIR}"
    fi
    if [ -d "${NEW_CONFIG_DIR}" ] && [ ! -e "${OLD_CONFIG_DIR}" ]; then
        log "Creating compat symlink ${OLD_CONFIG_DIR} -> ${NEW_CONFIG_DIR}"
        ln -s "${NEW_CONFIG_DIR}" "${OLD_CONFIG_DIR}"
    fi
    if [ -d "${NEW_ASSETS_DIR}" ] && [ ! -e "${OLD_ASSETS_DIR}" ]; then
        log "Creating compat symlink ${OLD_ASSETS_DIR} -> ${NEW_ASSETS_DIR}"
        ln -s "${NEW_ASSETS_DIR}" "${OLD_ASSETS_DIR}"
    fi
}

# The ansible role is responsible for installing the new sudoers file;
# this just removes the old one if present. Best-effort: if sudo prompts
# (timestamp expired) and we're in a non-interactive context, swallow
# the failure rather than aborting after the directory renames already
# succeeded. The ansible role removes the file too (`state: absent`),
# so failure here is recoverable.
remove_legacy_sudoers() {
    if [ -f /etc/sudoers.d/screenly_overrides ]; then
        log "Removing legacy /etc/sudoers.d/screenly_overrides"
        sudo -n rm -f /etc/sudoers.d/screenly_overrides 2>/dev/null \
            || log "Could not remove legacy sudoers file (no NOPASSWD); ansible will retry."
    fi
}

main() {
    if [ "${HOST_MODE}" = true ]; then
        self_relocate_if_needed "$@"
        migrate_repo_dir
    fi

    migrate_config_dir
    migrate_assets_dir

    if [ "${HOST_MODE}" != true ]; then
        recover_broken_data
    fi

    create_back_compat_symlinks

    if [ "${HOST_MODE}" = true ] \
        && { [ "$(id -u)" -eq 0 ] || command -v sudo >/dev/null 2>&1; }; then
        remove_legacy_sudoers
    fi
}

main "$@"
