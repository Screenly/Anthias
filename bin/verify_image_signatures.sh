#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

# Check that every image the stack is about to run was signed by the
# release pipeline that is supposed to have built it.
#
# Usage: verify_image_signatures.sh IMAGE [IMAGE ...]
#
# Two publishers, each with its own keyless cosign identity (Sigstore,
# via the publishing workflow's GitHub OIDC token):
#
#   * ghcr.io/screenly/anthias-* is built and signed by docker-build.yaml
#     in this repository. Only that exact workflow running on master is
#     accepted, so an image pushed by hand, by a fork, or by any other
#     workflow in the repository fails, even when whoever pushed it held
#     write access to the registry. cosign also accepts the
#     build-provenance attestation the same job pushes, which is signed
#     under the same identity and so proves the same thing; that is what
#     lets images built before signing was added still pass.
#
#   * ghcr.io/screenly-labs/wifi-connect is upstream's image, signed by
#     its release.yml for the git tag it was cut from. The identity is
#     derived from the pinned image tag (2026.10.13 must come from
#     refs/tags/v2026.10.13), so a signed image from some other release
#     re-pushed under the pinned tag fails too. This one matters most: it
#     runs with host networking.
#
# The check runs against the digest the local daemon actually pulled,
# not the tag: a tag can be moved between `compose pull` and this script
# and the daemon would not notice, whereas a digest names exactly the
# bytes that are about to run.
#
# Images from any other publisher (the Caddy sidecar that enable_ssl.sh
# adds, say) are not ours to vouch for, and are skipped.
#
# Exits non-zero if any of those images is unsigned, signed by someone
# else, or can't be checked at all, and names each one on stderr.

set -u

SIGNER_ISSUER='https://token.actions.githubusercontent.com'
ANTHIAS_REPO_PATTERN='^ghcr\.io/screenly/anthias-[a-z0-9-]+$'
ANTHIAS_SIGNER='https://github.com/Screenly/Anthias/.github/workflows/docker-build.yaml@refs/heads/master'
WIFI_CONNECT_REPO='ghcr.io/screenly-labs/wifi-connect'
WIFI_CONNECT_SIGNER_PREFIX='https://github.com/Screenly-Labs/wifi-connect/.github/workflows/release.yml@refs/tags/v'
CALVER_PATTERN='^[0-9]{4}\.[0-9]{1,2}\.[0-9]+$'

# See purge_stale_images.sh: the installer runs us before the user's
# `docker` group membership is live.
DOCKER=(sudo docker)

if [[ "$#" -eq 0 ]]; then
    echo "Usage: $0 IMAGE [IMAGE ...]" >&2
    exit 2
fi

if ! command -v cosign > /dev/null 2>&1; then
    echo "cosign is not installed, so the Anthias images can't be" \
         "verified. Re-run the Anthias installer to install it." >&2
    exit 1
fi

# Print the certificate identity IMAGE must be signed under. Prints
# nothing for an image we don't vouch for, and returns 1 for one we do
# but whose tag can't be tied to a release.
function expected_signer() {
    local image="$1"
    local repo="${image%:*}"
    local tag="${image##*:}"

    if [[ "${repo}" =~ ${ANTHIAS_REPO_PATTERN} ]]; then
        echo "${ANTHIAS_SIGNER}"
    elif [[ "${repo}" == "${WIFI_CONNECT_REPO}" ]]; then
        # Upstream releases are CalVer tags; anything else (`latest`, a
        # floating `2026.10`) has no single release to have signed it.
        if [[ ! "${tag}" =~ ${CALVER_PATTERN} ]]; then
            return 1
        fi
        echo "${WIFI_CONNECT_SIGNER_PREFIX}${tag}"
    fi
    return 0
}

# Print the repo@digest the local daemon holds for IMAGE, or nothing.
function pulled_digest() {
    local image="$1"
    local repo="${image%:*}"
    "${DOCKER[@]}" image inspect \
        --format '{{range .RepoDigests}}{{println .}}{{end}}' \
        "${image}" 2> /dev/null \
        | grep -m1 -F "${repo}@sha256:"
    # The caller reads the output, not the status: no match prints nothing.
    return 0
}

failed=()
for image in "$@"; do
    if ! signer=$(expected_signer "${image}"); then
        echo "Can't verify ${image}: its tag is not a pinned release." >&2
        failed+=("${image}")
        continue
    fi
    if [[ -z "${signer}" ]]; then
        continue
    fi

    ref=$(pulled_digest "${image}")
    if [[ -z "${ref}" ]]; then
        echo "Can't verify ${image}: no pulled digest for it." >&2
        failed+=("${image}")
        continue
    fi

    # Keep cosign's stderr for the failure message; its stdout is the
    # verified payload, which nobody here needs to read.
    if error=$(cosign verify \
        --certificate-identity "${signer}" \
        --certificate-oidc-issuer "${SIGNER_ISSUER}" \
        "${ref}" 2>&1 > /dev/null); then
        echo "Verified signature on ${image} (${ref#*@})"
    else
        echo "Signature verification FAILED for ${image} (${ref}):" >&2
        echo "${error}" >&2
        failed+=("${image}")
    fi
done

if [[ "${#failed[@]}" -gt 0 ]]; then
    exit 1
fi
exit 0
