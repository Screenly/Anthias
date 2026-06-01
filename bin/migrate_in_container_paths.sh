#!/bin/bash
#
# In-container companion to bin/migrate_legacy_paths.sh.
#
# The host-side bin/migrate_legacy_paths.sh (screenly -> anthias rename)
# only runs on apt/ansible installs, never on balena. So a pre-rebrand
# balena device keeps its data in /data/.screenly + /data/screenly_assets
# while the post-rebrand container reads /data/.anthias + /data/anthias_assets.
# Run the exact same migration against the container's /data volume so the
# balena upgrade path is covered by the one canonical migration (rather
# than a parallel reimplementation). DATA mode skips the host-only repo
# rename and sudoers cleanup, and additionally recovers devices that
# already booted the broken release with an empty anthias.db.
#
# Idempotent and safe to run on every container start.

set -euo pipefail

exec "$(dirname "$(readlink -f "$0")")/migrate_legacy_paths.sh" /data
