#!/usr/bin/env bash
#
# Cut the Anthias 2026.07.4 release — run this AS vpetersson.
# Follows the cut-release runbook: wait for the master Docker build to go
# green across every board, create a draft release with auto-generated
# notes targeting the full bump SHA, then (after you confirm) publish —
# which triggers the balena disk-image + OTA pipeline.
#
# Prereq: gh authenticated as vpetersson (NOT vpetersson-bot — the bot
# lacks release/deploy permissions and the release must be attributed to
# the maintainer).  Switch with:  gh auth switch --user vpetersson
#
set -euo pipefail

REPO="Screenly/Anthias"
VER="2026.07.4"          # zero-padded CalVer
TAG="v${VER}"

# --- 0. Guard: must be running as vpetersson --------------------------------
WHO="$(gh api user --jq .login)"
if [ "$WHO" != "vpetersson" ]; then
  echo "ERROR: gh is authenticated as '$WHO', not 'vpetersson'." >&2
  echo "       gh auth switch --user vpetersson   (or: gh auth login)" >&2
  exit 1
fi

# --- 1. Resolve the FULL 40-char SHA of the bump commit on master -----------
# (a short hash is rejected as target_commitish by the release API)
SHA="$(gh api "repos/$REPO/commits/master" --jq .sha)"
MSG="$(gh api "repos/$REPO/commits/$SHA" --jq '.commit.message' | head -1)"
echo "master HEAD : $SHA"
echo "commit      : $MSG"
case "$MSG" in
  *"bump version metadata to $VER"*) : ;;
  *) echo "ERROR: master HEAD is not the $VER version bump — aborting." >&2; exit 1 ;;
esac

# --- 2. Wait for the master Docker build to be GREEN across all boards ------
# The release preflight requires every ghcr.io/.../<shorthash>-<board> image;
# the 32-bit armhf (pi2/pi3) QEMU legs finish last, so wait for the whole run.
echo "Waiting for docker-build.yaml on $SHA to finish (armhf legs finish last)..."
while :; do
  LINE="$(gh run list --repo "$REPO" --workflow=docker-build.yaml --branch master \
            --json headSha,status,conclusion \
            --jq "[.[] | select(.headSha==\"$SHA\")][0] | \"\(.status)|\(.conclusion)\"")"
  STATUS="${LINE%%|*}"; CONCL="${LINE##*|}"
  echo "  build: ${STATUS:-<run not created yet>} ${CONCL:-}"
  [ "${STATUS:-}" = "completed" ] && break
  sleep 60
done
if [ "$CONCL" != "success" ]; then
  echo "ERROR: master build concluded '$CONCL', not success." >&2
  echo "       Fix the failing board leg before cutting the release." >&2
  exit 1
fi
echo "Master build green across all boards."

# Optional belt-and-suspenders (needs docker + ghcr access):
# SHORT="$(git rev-parse --short=7 "$SHA")"
# for b in pi5 pi4-64 pi3-64 pi2 pi3 x86 arm64; do
#   for s in server viewer redis; do
#     docker buildx imagetools inspect "ghcr.io/screenly/anthias-$s:$SHORT-$b" >/dev/null \
#       || { echo "MISSING image $s:$SHORT-$b"; exit 1; }
#   done
# done

# --- 3. Create the release as a DRAFT with auto-generated notes -------------
# Draft avoids triggering the deploy pipeline while notes are reviewed.
if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
  echo "Release $TAG already exists — skipping create (will re-verify before publish)."
else
  gh release create "$TAG" --repo "$REPO" \
    --draft --generate-notes --title "$VER" --target "$SHA"
  echo "Draft release $TAG created with auto-generated notes."
fi

# --- 4. (Optional) layer a curated summary on top of the generated notes ----
# gh release view "$TAG" --repo "$REPO" --json body -q .body > "/tmp/notes-$VER.md"
# $EDITOR "/tmp/notes-$VER.md"   # prepend a "## Highlights" section
# gh release edit "$TAG" --repo "$REPO" --notes-file "/tmp/notes-$VER.md"

# --- 5. Publish (triggers balena disk-image + OTA) --------------------------
echo
echo "About to PUBLISH $TAG as latest. This triggers build-balena-disk-image.yaml"
echo "(disk images + fleet OTA) — an outward-facing, hard-to-undo action."
read -r -p "Publish now? [y/N] " ans
if [ "$ans" != "y" ] && [ "$ans" != "Y" ]; then
  echo "Left as a draft. Publish later with:"
  echo "  gh release edit $TAG --repo $REPO --draft=false --latest"
  exit 0
fi
gh release edit "$TAG" --repo "$REPO" --draft=false --latest

echo
echo "Published $TAG. Watch the deploy pipeline:"
echo "  gh run list --repo $REPO --workflow=build-balena-disk-image.yaml"
echo "Transient 'balena os download ... ERR_STREAM_PREMATURE_CLOSE' → rerun:"
echo "  gh run rerun <run-id> --failed --repo $REPO"
