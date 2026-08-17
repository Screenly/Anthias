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

set -u

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
    local reclaimed
    reclaimed=$(
        "${DOCKER[@]}" image prune -f 2> /dev/null \
            | sed -n 's/^Total reclaimed space: //p'
    )
    # `prune` reports 0B on a device with nothing to sweep, and a quiet
    # run should stay quiet.
    if [ -n "${reclaimed}" ] && [ "${reclaimed}" != '0B' ]; then
        echo "Reclaimed ${reclaimed} of untagged images."
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

purge_tagged_images "${IN_USE}"
purge_untagged_images

exit 0
