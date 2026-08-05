---
name: cut-release
description: Step-by-step runbook for cutting a tagged Anthias release — bump the CalVer version across all relevant manifests/lockfiles, merge, wait for the master Docker build, tag, and publish a GitHub release (auto-generated notes first, then layered with a curated summary). Use when the task is to ship a new versioned release. For the underlying "why" (balena topology, OTA-only deploys, CI internals) see the anthias-release skill.
---

# Cut an Anthias release

Anthias versions are **CalVer `YYYY.0M.MICRO`** (zero-padded month). This is the
actionable release runbook; the reference facts behind each step live in the
**anthias-release** skill.

> **Sequencing is load-bearing.** Publishing the GitHub release triggers the
> balena disk-image + OTA pipeline, whose preflight verifies the per-board
> container images already exist in GHCR. Those are built by the master-push
> Docker build. So you must **merge the bump, wait for the full master Docker
> build to go green across every board, and only then publish the release.**
> Publishing early fails preflight (a guard catches it — no harm — but the
> deploy jobs skip).

## 0. Pick the version

- CalVer `YYYY.0M.MICRO`. **Check the current month** — a new month resets MICRO
  to `0` (the release after `2026.05.2` is `2026.06.0`, not `2026.05.3`).
- Two spellings of the same number, and you need both:
  - **zero-padded** `2026.06.0` — for `package.json`, `pyproject.toml`, the git tag.
  - **strict semver, no leading zero** `2026.6.0` — for `uv.lock` (SemVer §9;
    balena also requires this form, rendered from pyproject at deploy time).

## 1. Bump version metadata (all relevant files)

The release bump touches exactly **three** files (confirmed against every prior
`chore(release)` commit). `bun.lock` carries no root version — do **not** touch it.

1. `package.json` → `"version": "2026.06.0"` (zero-padded)
2. `pyproject.toml` → `version = "2026.06.0"` (zero-padded)
3. `uv.lock` → under the `name = "anthias"` package block, `version = "2026.6.0"`
   (**strict semver, no leading zero**). Bump this line directly, or regenerate
   with `uv lock` and confirm only the anthias version line changed.

Sanity check before committing — these must be the only version edits:

```bash
git diff -- package.json pyproject.toml uv.lock
grep -n '"version"' package.json; grep -n '^version' pyproject.toml
grep -n -A1 'name = "anthias"' uv.lock
```

## 2. PR → merge to master

- Branch, commit as `chore(release): bump version metadata to <ver>` (the
  established convention), open a PR ready-for-review, let CI pass, merge to
  `master`. Admin can merge past `REVIEW_REQUIRED` with `gh pr merge --squash
  --admin` (`enforce_admins=false` on master).

## 3. Wait for the master Docker build (ALL boards)

- The merge to `master` triggers `docker-build.yaml`, which builds and pushes
  `ghcr.io/screenly/anthias-{server,viewer,redis}:<short-hash>-<board>` for every
  board. **The 32-bit armhf (Pi 2 / Pi 3) jobs run under QEMU and finish last** —
  wait for the whole matrix.
- Track it: `gh run list --workflow=docker-build.yaml --branch master` → watch
  the run for the bump commit to conclude success.
- Optional preflight (what the release pipeline checks): for each board,
  `docker buildx imagetools inspect ghcr.io/screenly/anthias-server:<short-hash>-<board>`
  should resolve (repeat for viewer/redis; include `arm64` for Rock Pi 4).

## 4. Tag + create the GitHub release — auto-generated notes FIRST

Create the release as a **draft** with GitHub's auto-generated notes. Target the
**full** commit SHA (a short hash is rejected as `target_commitish`); draft
avoids triggering the deploy pipeline while you edit.

```bash
SHA=$(git rev-parse origin/master)        # full 40-char SHA of the merged bump
gh release create v2026.06.0 \
    --draft --generate-notes \
    --title 2026.06.0 \
    --target "$SHA"
```

`--generate-notes` populates the body with GitHub's PR-based changelog (the
"first round").

**A draft release does NOT create the tag.** GitHub defers tag creation until the
release is published, so after this command `git ls-remote --tags upstream
refs/tags/v2026.06.0` returns nothing and the release URL is a placeholder
(`releases/tag/untagged-<hash>`). That is a feature: nothing is outward-facing
yet, so if QA turns up a blocker you can delete the draft without burning a
version number or deleting a published tag. The tag appears — at the
`--target` SHA — at step 6.

Also note the board matrix is 7 boards (`pi2`, `pi3`, `pi3-64`, `pi4-64`, `pi5`,
`x86`, `arm64`); there is no `pi1` or 32-bit `pi4` image, so a preflight loop that
invents those tags will report false gaps. On the release-asset side only 4 Pi
boards get an rpi-imager entry and the 32-bit `pi3` has no per-board `.json` (the
imager's "Raspberry Pi 3" entry uses the 64-bit image) — expected, not a gap.

## 5. Layer our curated notes on top (keep the auto-generated list)

Capture the generated notes, prepend a short human summary of the highlights,
and update — do **not** discard the auto-generated changelog:

```bash
gh release view v2026.06.0 --json body -q .body > /tmp/notes.md
# Prepend a curated "## Highlights" section above the generated list,
# then write the combined file back:
gh release edit v2026.06.0 --notes-file /tmp/notes.md
```

Keep the curated summary short; link notable PRs/releases as markdown links.

## 6. Publish → the deploy pipeline runs

```bash
gh release edit v2026.06.0 --draft=false --latest
```

Flipping to published (`release: published`, `startsWith(tag,'v')`) triggers
`build-balena-disk-image.yaml`:

- **`preflight`** re-verifies every `<short-hash>-<board>` image exists in GHCR
  (this is why step 3 must be fully green first).
- **`balena-build-images`** builds bootable disk images for fresh flashes.
- **`balena-cloud-deploy`** is the actual fleet OTA (independent of the disk
  images — OTA can be green while a disk-image job fails).

## 7. Verify / recover

- Watch the run: `gh run list --workflow=build-balena-disk-image.yaml`.
- **Transient disk-image failures → rerun, don't debug.** `balena os download
  --version latest` intermittently dies with `ERR_STREAM_PREMATURE_CLOSE` (a CDN
  blip); `gh run rerun <run-id> --failed` fixes it (also re-runs the skipped
  `upload-release-assets` + `build-rpi-imager-json`).
- If preflight failed because you published too early, re-trigger after the
  master build is green: `gh workflow run build-balena-disk-image.yaml -f
  tag=v2026.06.0`.

## Notes

- **Deploying master to fleets WITHOUT a release** is a different flow —
  `bin/balena_ota_deploy.sh <board> <ver> <short-hash>` per board (see the
  anthias-release skill). That leaves no git tag / GH release / disk image;
  cutting the release later is a separate explicit step.
- CI derives the version from the tag (`${TAG#v}`); local deploy greps
  `[project].version` from `pyproject.toml`. They agree because CI checks out at
  the tag — so keep the tag and pyproject in lockstep.
