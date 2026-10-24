#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

# Reclaim the disk held by Anthias images the current stack no longer
# uses.
#
# Every upgrade pulls a fresh `<tag>-<device-type>` image for each
# service and leaves the outgoing release behind in the local image
# store. Nothing ever collects it: `docker system prune -f` (which
# install.sh already runs) only reclaims *dangling* layers, and the
# superseded images are still tagged. A device that has been through a
# handful of upgrades therefore carries several complete copies of the
# stack. Devices in the field were found holding roughly 16 GB of dead
# images, most of a 32 GB card. See issue #3291.
#
# `docker image prune --all` is the blunt version of this, but plenty of
# operators run their own containers next to Anthias on the same box and
# pruning everything unreferenced takes their images too. So the sweep of
# *tagged* images is restricted to the Anthias repositories, and only
# removes an image that no container references, running or stopped. The
# stack that was just brought up is never a candidate, and neither is an
# image belonging to a stopped container the operator is keeping around.
# Images that have lost every reference are handled separately, by plain
# `docker image prune`; see purge_untagged_images for why they cannot be
# attributed to Anthias.
#
# Safe to run at any time, and safe to run twice: a device with nothing
# to reclaim prints nothing.
#
# `--untagged-only` narrows the run to the second of those two sweeps.
# It exists for the upgrade's failure path, where the superseded images
# are the release the device is still running and must survive, but the
# remains of the pull that just died are exactly what needs collecting.

set -u

UNTAGGED_ONLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --untagged-only)
            UNTAGGED_ONLY=1
            ;;
        *)
            echo "Usage: $0 [--untagged-only]" >&2
            exit 2
            ;;
    esac
    shift
done

# Repositories Anthias images have been published under. The Docker Hub
# names predate the move to GHCR and only linger on devices installed
# before it, where they are dead weight by definition.
ANTHIAS_REPOS=(
    'ghcr.io/screenly/anthias-*'
    'screenly/anthias-*'
    'screenly/srly-ose-*'
)

# The installer runs this before the anthias user's new `docker` group
# membership is live in the current session, which is also why
# upgrade_containers.sh drives compose through sudo.
DOCKER=(sudo docker)

# containerd's content store. A pull that dies partway abandons data
# here in two forms — part-written layers under `ingest/`, and whole
# layers under `blobs/` that finished downloading before the pull as a
# whole gave up — and neither is reachable from any image. `docker image
# prune` collects both without counting either; see
# purge_untagged_images. Root owns the directory 0700, hence sudo.
# Overridable for a host that has moved containerd's state off the
# default path.
CONTENT_DIR="${ANTHIAS_CONTAINERD_CONTENT_DIR:-\
/var/lib/containerd/io.containerd.content.v1.content}"

function purge_tagged_images() {
    local in_use="$1"
    local filters=()
    local repo ref id target

    for repo in "${ANTHIAS_REPOS[@]}"; do
        filters+=(--filter "reference=${repo}")
    done

    while read -r ref id; do
        [ -n "${id}" ] || continue

        if grep -qxF "${id}" <<< "${in_use}"; then
            continue
        fi

        # A `repo:<none>` row is an Anthias image that still carries a
        # digest reference but has lost its tag, which is how the
        # classic image store leaves the outgoing release on a device
        # tracking a moving tag. There is no `repo:<none>` reference to
        # remove, so go by ID. Docker declines that when the ID also
        # answers to a tag in some other repository, which is the
        # outcome we want anyway.
        target="${ref}"
        if [[ "${ref}" == *':<none>' ]]; then
            target="${id}"
        fi

        # Announce after the fact, not before: docker has the last word
        # on whether an image can go, and it turns down anything we
        # misjudged as unused.
        if "${DOCKER[@]}" rmi "${target}" > /dev/null 2>&1; then
            echo "Reclaimed superseded image ${ref}"
        fi
    done < <("${DOCKER[@]}" images --no-trunc "${filters[@]}" \
        --format '{{.Repository}}:{{.Tag}} {{.ID}}')
}

function content_store_size_kb() {
    # 0 rather than empty when the directory is absent, unreadable, or
    # this is a classic image store with no content directory at all:
    # every caller wants a number it can subtract, and "nothing there"
    # and "cannot tell" are the same answer for our purposes.
    local size
    size=$(sudo du -sk "${CONTENT_DIR}" 2> /dev/null | awk '{print $1}')
    echo "${size:-0}"
}

function human_readable_kb() {
    local kb="$1"
    if [ "${kb}" -ge 1048576 ]; then
        awk -v kb="${kb}" 'BEGIN { printf "%.1f GB", kb / 1048576 }'
    elif [ "${kb}" -ge 1024 ]; then
        awk -v kb="${kb}" 'BEGIN { printf "%.0f MB", kb / 1024 }'
    else
        echo "${kb} kB"
    fi
}

function purge_untagged_images() {
    # The pass above can only see images that still answer to an Anthias
    # repository name. Under the containerd image store (Docker 25 and
    # later), re-pulling a moving tag such as `latest-pi4-64` drops every
    # reference the outgoing image had, tag and digest alike, so there is
    # nothing left to identify it by. Untagged images are unreachable by
    # name for everyone, not just us, and `prune` without `--all` only
    # ever touches those, never a tagged image and never one a container
    # references. It is also the same sweep the installer has always run
    # as part of its cleanup step, so this only widens it to operators
    # who upgrade by calling bin/upgrade_containers.sh directly.
    local reclaimed before after freed
    before=$(content_store_size_kb)
    reclaimed=$(
        "${DOCKER[@]}" image prune -f 2> /dev/null \
            | sed -n 's/^Total reclaimed space: //p'
    )
    after=$(content_store_size_kb)

    # `prune` reports 0B on a device with nothing to sweep, and a quiet
    # run should stay quiet.
    if [ -n "${reclaimed}" ] && [ "${reclaimed}" != '0B' ]; then
        echo "Reclaimed ${reclaimed} of untagged images."
    fi

    # The same sweep also clears what a dead pull abandoned in the
    # content store, but its total only counts whole images, so it stays
    # at `0B` while hundreds of MB go (issue #3410) — the one number an
    # operator short of space needs is the one it will not print.
    # Measured on a Pi 5: an interrupted pull left 130 MB of part-written
    # layers and 39 MB of orphaned whole ones; `prune` freed all 169 MB
    # and reported `0B`. Measuring the store ourselves is what makes that
    # reclaim visible, and it is exact where a free-space delta would be
    # at the mercy of anything else writing to the card.
    freed=$((before - after))
    if [ "${freed}" -gt 0 ]; then
        echo "Reclaimed $(human_readable_kb "${freed}") left behind by an" \
             "interrupted download."
    fi
}

# Bail out rather than guess if we cannot enumerate containers: without
# that list every Anthias image looks unused, including the ones the
# running stack depends on. (Docker itself refuses to untag an image a
# container still references, so this is belt-and-braces.)
if ! CONTAINERS=$("${DOCKER[@]}" ps -aq 2> /dev/null); then
    echo "Docker is not reachable. Skipping stale image cleanup." >&2
    exit 0
fi

IN_USE=''
if [ -n "${CONTAINERS}" ]; then
    # Word splitting is deliberate: one ID per line from `docker ps -aq`.
    # shellcheck disable=SC2086
    IN_USE=$("${DOCKER[@]}" inspect --format '{{.Image}}' ${CONTAINERS} \
        2> /dev/null)
fi

if [ "${UNTAGGED_ONLY}" -eq 0 ]; then
    purge_tagged_images "${IN_USE}"
fi
purge_untagged_images

exit 0
