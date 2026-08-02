---
name: testbed-qa
description: How to validate Anthias changes on the physical hardware testbeds — the /tmp/used-by lockfile protocol, deploying images (plain docker-compose, not balena), the pure-Python overlay method, force-display on headless eglfs, and per-board memory/hardware quirks. Use when testing a viewer/server/webview change on real Pi / x86 / Rock Pi 4 hardware.
---

# Testbed QA runbook

A reusable procedure for validating Anthias changes on the physical testbeds.
Fleet coverage is one board per architecture: **armhf** (Pi 2, Pi 3 32-bit),
**arm64** (Pi 3 64-bit, Pi 4, Pi 5), **x86**, and a **Rock Pi 4** (generic
arm64). All testbeds are headless. SSH is `ssh <USER>@<BOARD_IP>` throughout;
substitute the target board's address (kept out of the repo — it is environment
state, not code).

## 1. Claim the board — the `/tmp/used-by` lockfile protocol

Testbeds are shared across concurrent agent sessions. Coordinate via a lockfile
at **`/tmp/used-by`** on the device (it holds the current holder's agent id).
Two sessions on one viewer collide — the classic symptom is the container being
restarted out from under you, wiping `/dev/shm` scratch and contaminating
captures.

**IRON RULE: never write `/tmp/used-by` before reading it.** Every claim is
read-check-then-write, including re-acquiring after your *own* release. A blind
`echo "myid" > /tmp/used-by` is BANNED.

- **Read first.** `cat /tmp/used-by`. If it holds *another* agent's id, BACK
  OFF — do not touch the box, do not overwrite the lock. Poll read-only until it
  clears, then claim.
- **Claim only when free/yours.** Guard the write, e.g.:
  `L=$(cat /tmp/used-by 2>/dev/null); [ -z "$L" ] && echo "$MYID ..." > /tmp/used-by || echo "HELD: $L — backing off"`
- **The re-acquire is NOT exempt.** Between your release and your re-acquire
  (e.g. while a long build ran) another agent may have taken it. Re-read before
  re-writing; assume they grabbed it until the read proves otherwise.
- **Release when done.** Clear or `rm /tmp/used-by` so others can take it.
- **Restore what you changed** before releasing: swapped `AnthiasViewer` binary,
  injected assets, config edits. Keep a pristine copy at `AnthiasViewer.orig`; a
  binary hot-swap hits "Text file busy" while the process runs and needs a
  container restart to take effect.
- **Recovery if you clobbered someone:** stop immediately. If the lock still
  shows your (wrongly-written) id, `rm` it to clear the field. Never touch it if
  it now shows theirs.

## 2. Deploy a version (plain docker-compose, NOT balena)

Testbeds run **published GHCR images** via docker compose at `~/anthias`
(project `anthias`), pinned by a `<short-hash>-<board>` tag in the device's
generated `docker-compose.yml`. Their git checkouts may sit on WIP branches with
uncommitted edits, but the running containers are official GHCR builds,
decoupled from that source. Board→tag suffix examples: Pi 4 = `pi4-64`,
Pi 5 = `pi5`, Rock Pi 4 = `arm64`, x86 = `x86`. "Latest" = master HEAD short
hash (`git rev-parse --short=7`); CI publishes `<hash>-<board>` for
server/viewer/redis on merge to master.

Deploy (non-destructive, preserves the board's WIP git state):

1. `cp docker-compose.yml docker-compose.yml.bak-predeploy-<hash>`
2. `sed -i -E 's#(ghcr\.io/screenly/anthias-(server|viewer|redis):)[0-9a-zA-Z]+-#\1<hash>-#g' docker-compose.yml`
   (the `[0-9a-zA-Z]+-` eats only the hash prefix, leaving a hyphenated board
   suffix like `pi4-64` intact)
3. `sudo docker compose pull`
4. `sudo docker compose up -d --remove-orphans`
5. `sudo docker image prune -af` (running images are kept)

Verify: `curl -sw '%{http_code}' localhost/api/v2/assets` → 200; then read the
viewer logs.

**Streaming a locally-built image** (skip GHCR): `docker save <img> | ssh
<USER>@<BOARD_IP> 'sudo docker load'` — **no gzip**. On a fast LAN the wire is
faster than the CPU can (de)compress, so a gzip pipe makes the transfer *slower*.
Streaming also avoids an intermediate multi-GB file on tight device storage.
Reserve `scp` for small files (compose templates).

**Small-SD disk-fill gotcha:** SD/eMMC cards are small and often 80–90% full. A
fresh viewer+server pull adds several GB of layers BEFORE the old images are
freed, briefly hitting 100% (which produces dbus `No space left on device`
errors). On tight boards, **prune unused images first** to free room, then pull,
then prune again. Rule of thumb: remove the old image before loading the new one.

**Watcher pitfall:** a `pgrep -f "docker compose pull"` polling loop matches its
own ssh command line and reports the job "running" forever. Confirm completion
by side effect (images present, disk stable), not by pgrep.

## 3. Validate a pure-Python fix without an image rebuild (overlay method)

For server/viewer/common Python changes on a full-stack testbed:

1. Build a combined tree, `tar czf` only the changed `src/` files
   (`git diff --name-only origin/master..HEAD -- src/`).
2. `scp` the tarball over, then per container:
   `docker cp fixes.tgz <c>:/tmp/ && docker exec <c> tar xzf /tmp/fixes.tgz -C /usr/src/app`
   (code lives at `/usr/src/app/src`, editable-installed, so a `docker restart`
   picks it up).
3. Validate: `docker exec <server> python <script>` (set
   `DJANGO_SETTINGS_MODULE=...settings` for logic checks); `curl
   http://localhost/api/...` for HTTP. To exercise the viewer's mtime-gated
   playlist reload, inject rows via `Asset.objects.create` then
   `touch ~/.anthias/anthias.db`.
4. **Restore to pristine:** `cd ~/anthias && docker compose up -d
   --force-recreate <services>` drops the overlay writable layer back to the
   pinned image. Delete any injected assets first.

## 4. Force-display on headless eglfs boards

The eglfs boards (Pi 3 64-bit, Pi 4) refuse to start the viewer when headless —
`wait_for_eglfs_display` blocks until a DRM connector reads `connected`. Correct
signage behavior, but it means a headless testbed renders nothing and you cannot
verify the eglfs pipeline. The debugfs `force` attribute alone does NOT flip
status on vc4-kms. The reliable way is a kernel cmdline modifier + reboot:

```
# /boot/firmware/cmdline.txt — append on the single line (space-separated):
video=HDMI-A-1:1920x1080@60e     # trailing 'e' forces the connector enabled/connected
```

After reboot the connector reads `connected`, the viewer starts, and video
HW-decodes (`/dev/video10`, frames in playback-stats). Revert by restoring the
`.bak-qa` copy and rebooting. Wayland boards (Pi 5, x86, Rock Pi 4) and linuxfb
(Pi 2) do NOT need this — cage tolerates a missing output and linuxfb has
`/dev/fb0` regardless. Since all testbeds are headless, you are always verifying
the *software pipeline* (decode→sink→fb/compositor), not physical HDMI pixels.

## 5. Standing rules

- **Never heavy-compute on an SBC.** No ffmpeg transcode, image build, or big
  compression on a testbed — it is glacial, starves the viewer under test, and
  skews the measurement. Produce test media/artifacts on the dev host and copy
  them over.
- **Never broad-pkill on the dev host.** The dev host runs many concurrent
  Docker services across worktrees; loose patterns (`pkill -f buildx`) cascade
  into unrelated containers. Target by exact PID (`kill $(pgrep -fa "<unique
  substring>")` after confirming) or cancel a harness-spawned background job by
  its task id. List before killing.
- **No persistent logs in `/tmp`.** It is tmpfs and is wiped on reboot — exactly
  the event a hotplug/power logger is trying to capture. Write to the user's home
  or `/var/log`.
- **Disable idle testbeds' assets.** After a run, PATCH every asset
  `{"is_enabled": false}` via the v2 API so the viewer idles to black.
  Re-enable only the board currently under measurement. Idle rotation wears the
  SD card, keeps the decoder/GPU warm (noise), and visually conflates "testing
  now" with "yesterday's run".
- **Validate arch-specific fixes on REAL target hardware.** An armhf-image-on-a-
  Pi-4-host proxy has a different RAM ceiling, CPU, thermal and timing profile —
  it undersells (or misses) the bug. Use the proxy only when no real unit is
  reachable, and say so explicitly in the PR.
- **A single-process / offscreen / linuxfb container is NOT a valid repro** for
  Cortex-A7 (armhf) crashes. Such a harness takes a different code path and
  failed to reproduce a field-known SIGBUS even on the A7 where the crash
  definitely exists, so it is worthless as a negative control anywhere. The field
  crash lives in the FULL multi-process WebEngine init on a real display.
- **E2E means the full integrated stack on a real board.** Do not tick a PR's
  end-to-end box after a component-level harness run. The integrated run (real
  service spawning the real asset-rotation → player path) is where a whole class
  of bugs — arg plumbing, service wiring, audio defaults, rotation — surfaces
  that a standalone module never shows.

## 6. Per-board memory / hardware quirks

- **Low-RAM boards OOM-wedge on the latest viewer even at idle.** The 1 GB
  Rock Pi 4 could not settle the latest viewer image even with an empty playlist
  — the merged Chromium/QWebEngine baseline alone exceeds the ~1 GB ceiling; it
  wedged into an SSH banner-exchange timeout and needed physical power cycles.
  Read any "wedge / drop / SSH unresponsive" on a 1 GB board as memory pressure
  first, not CPU. To exercise an arm64 code path, override `DEVICE_TYPE=arm64`
  on a stable board (Pi 4 / Pi 5) instead.
- **512 MB is too little for Anthias.** On a 512 MB Pi 3 A+ the official
  installer dogfoods cleanly, but the running stack OOM-wedges: idle already
  leans on swap, and the moment the viewer loads a **webpage** the QtWebEngine
  (Chromium) spike exhausts RAM+swap until `sshd` and even `docker` stop
  responding. Mitigation for testing: `docker stop` celery and enable only a
  light image asset. (Image and video rotation still validate here; webpage
  paths do not.)
- **x86 disk EIO can wedge sshd.** A failing block device can throw EIO while
  Docker removes an old container's filesystem; the root fs then goes read-only,
  forked login shells die, and sshd resets every session at key exchange while
  ping/TCP-22 stay up. Recovery needs a physical power cycle (no remote power on
  testbeds). Treat repeat EIO as suspect storage hardware, not a script fault.
- **x86 headless capture needs `WLR_BACKENDS=headless`.** With no monitor, every
  DRM connector reads `disconnected`, cage has no `wl_output`, and `grim` fails
  with `no wl_output`. Add `- WLR_BACKENDS=headless` to the viewer service
  `environment:` and `up -d` it — cage then creates a virtual `HEADLESS-1`
  output you can screenshot. `grim` is not in the viewer image (`apt-get install
  -y grim` in the container); any `up -d` that recreates the viewer wipes it.
  Remove the env line on restore. When grabbing video frames, sample several
  spaced frames and check mean luma before concluding "black = broken" (pre-roll
  can be black).
- **SSH is pubkey-only to a non-root user.** Log in as `<USER>@<BOARD_IP>` with
  the installed SSH public key. The Pi boards set `PasswordAuthentication no`;
  probing with `-o PubkeyAuthentication=no`, `-o
  PreferredAuthentications=password`, or `sshpass` returns "Permission denied
  (publickey)" and *looks* locked out. It is not — drop the password flags and
  let pubkey auth run.
- **Fresh Debian x86 with a root password set ships no sudo / no sudo-group / no
  curl** (this is a website-doc gap, not an installer bug). Recovery as root:
  `su -` → `apt install -y sudo` → `usermod -aG sudo <USER>` → re-login. Debian
  only auto-installs sudo and adds the first user to the sudo group when the root
  password is left blank.
