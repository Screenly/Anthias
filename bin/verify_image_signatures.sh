#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

# Check that every Anthias image the stack is about to run was signed by
# our own release pipeline.
#
# Usage: verify_image_signatures.sh IMAGE [IMAGE ...]
#
# docker-build.yaml signs each ghcr.io/screenly/anthias-* image it pushes
# with keyless cosign (Sigstore, via the workflow's GitHub OIDC token).
# A signature only counts here if its certificate was issued to that
# exact workflow running on master. So an image pushed by hand, by a
# fork, or by any other workflow in the repository fails, even when
# whoever pushed it held write access to the registry. cosign also
# accepts the build-provenance attestation the same job pushes, which
# is signed under the same identity and so proves the same thing; that
# is what lets images built before signing was added still pass.
#
# The check runs against the digest the local daemon actually pulled,
# not the tag: a tag can be moved between `compose pull` and this script
# and the daemon would not notice, whereas a digest names exactly the
# bytes that are about to run.
#
# Images outside the Anthias repositories (the Caddy sidecar that
# enable_ssl.sh adds, say) are not ours to sign, and are skipped.
#
# Exits non-zero if any Anthias image is unsigned, signed by someone
# else, or can't be checked at all, and names each one on stderr.

set -u

SIGNER_IDENTITY='https://github.com/Screenly/Anthias/.github/workflows/docker-build.yaml@refs/heads/master'
SIGNER_ISSUER='https://token.actions.githubusercontent.com'
ANTHIAS_REPO_PATTERN='^ghcr\.io/screenly/anthias-[a-z0-9-]+$'

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
    repo="${image%:*}"
    if [[ ! "${repo}" =~ ${ANTHIAS_REPO_PATTERN} ]]; then
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
        --certificate-identity "${SIGNER_IDENTITY}" \
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
