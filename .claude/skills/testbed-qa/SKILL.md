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

1. `cp docker-compose.yml docker-compose.yml.bak-<CURRENT-tag>` — name the backup
   after the tag it **contains**, i.e. the one you will restore *to*, never after
   the tag you are about to deploy. See the warning below.
2. `sed -i -E 's#(ghcr\.io/screenly/anthias-(server|viewer|redis):)[0-9a-zA-Z]+-#\1<hash>-#g' docker-compose.yml`
   (the `[0-9a-zA-Z]+-` eats only the hash prefix, leaving a hyphenated board
   suffix like `pi4-64` intact)
3. `sudo docker compose pull`
4. `sudo docker compose up -d --remove-orphans`
5. `sudo docker image prune -af` (running images are kept)

Verify: `curl -sw '%{http_code}' localhost/api/v2/assets` → 200; then read the
viewer logs.

**`bak-predeploy-<hash>` is ambiguous and has already caused near-misses — always
`grep` a backup before restoring it.** The old wording named the backup after the
hash being *deployed*, so `docker-compose.yml.bak-predeploy-fbe83e9` holds
whatever was pinned *before* fbe83e9 — frequently `latest-<board>`. But agents
following the same wording have also read it the other way and produced files
named for their *contents*, so both conventions now exist side by side on the
fleet with opposite meanings. Live examples: on the Pi 4 and Rock Pi 4,
`bak-predeploy-fbe83e9` pins `latest-<board>`, while `bak-predeploy-fbe83e9-qa2`
on the same box really does pin `fbe83e9`. Restoring by filename alone silently
moves a board onto a floating `latest` tag — which then quietly invalidates the
next "baseline" measurement taken on it.

So: name new backups after their contents (step 1), and before *any* restore run
`grep -oE 'anthias-server:[0-9a-zA-Z]+-[a-z0-9-]+' <backup>` and confirm it is the
tag you actually want. Never trust the name.

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

**Never build the overlay from the shared working tree — extract from the commit
object.** The dev-host checkout is shared across concurrent sessions and *will*
be switched to another branch mid-run (observed twice in one validation, and the
branch under test also gained new commits while it was being tested). Use
`git archive <sha> -- src/ | tar x -C <staging>` (or `git show <sha>:<path>`) and
then assert the in-container blob md5 matches that commit, so the report names a
commit you can actually stand behind. Never `git checkout` on the shared tree to
set up your own run.

**Beware: the pinned image can be older than your branch base.** Overlaying
`<branch>`'s files onto an image built from an older commit also carries every
unrelated change to those files between the two. Diff
`<image-commit>..<branch-base>` for the files you overlay and say in the report
what else came along, or you are attributing a mixed result to one commit.

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
`.bak-qa` copy and rebooting. Wayland boards (Pi 5, x86, Rock Pi 4) do NOT need
this — cage tolerates a missing output. Since all testbeds are headless, you are
always verifying the *software pipeline* (decode→sink→fb/compositor), not
physical HDMI pixels.

**Don't decide by the board's nominal Qt platform — check the display driver.**
"linuxfb board" is not the same as "has `/dev/fb0`". The Pi 3 A+ (32-bit)
runs `QT_QPA_PLATFORM=linuxfb` but boots `dtoverlay=vc4-kms-v3d`, so headless it
has **no** `/dev/fb0` at all (DRM finds no CRTC → no fbdev emulation) and the
viewer sits on `no framebuffer (/dev/fb0) yet — waiting`. It needs the same
force-display treatment as the eglfs boards. The Pi 2 is the genuine legacy-fbdev
case and does have `/dev/fb0` regardless. Check `ls /dev/fb*` and
`grep dtoverlay /boot/firmware/config.txt` rather than assuming from the Qt
platform name.

Also note the forced mode is a *request*, not a guarantee: on both the Pi 3 A+
and the Pi 3 64-bit the fbdev/scanout surface came up **1024x768** despite
`video=HDMI-A-1:1920x1080@60e` on the cmdline and `resolution = 1920x1080` in
`anthias.conf`. Read the actual surface geometry (fb0 byte size, or the DRM mode)
before computing expected pixel geometry for a capture comparison.

**Check for a pre-existing modifier before you add one, and leave a `.bak-qa`.**
The Pi 3 64-bit carried a force-display cmdline from an earlier session for
weeks with no backup — which both means the board was silently *not*
representative of a headless device, and left later sessions with nothing safe to
restore. A `cmdline.txt.orig` is **not** a substitute: on that board `.orig` is
the imaging-time file (`console=tty1`, `ds=nocloud`, no cgroup-memory flags), so
"restoring" it would silently change the console and drop the cgroup flags the
stack needs.

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
- **`pkill -f` / `pgrep -f` match their own command line.** The self-match trap
  isn't only a `pgrep` watcher problem: a `pkill -f "<pattern>"` sent over SSH
  matches the pattern *inside its own ssh command line* and kills the session
  running it (observed mid-run; the board was unaffected but the agent lost its
  shell). Use an exact PID, or a bracketed pattern (`[m]y-sampler`) that cannot
  match the literal string in the invoking command.
- **No persistent logs in `/tmp`, and no large fixtures either.** `/tmp` is a
  ~948 MB tmpfs: it is wiped on reboot — exactly the event a hotplug/power logger
  is trying to capture — **and** it is mounted `usrquota` with the quota shared
  across every concurrent session. Filling it does more than fail a write: once
  the quota is hit, the harness's own Bash output capture breaks and even `echo`
  fails. Multi-megapixel fixtures blow the budget fast (a 24 MP BMP is 72 MB, a
  24 MP TIFF 95 MB), so build media and write logs under the user's home
  (`/home/<user>/<task>-<board>/`) or `/var/log`. Check with `df -h /tmp`; note
  hidden dirs (`.venv`-style) don't show up in `du -sh <dir>/*/`.
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
- **Never tick a PR's device-testing box from intent.** It goes back only with
  measured numbers attached. Writing the checklist before the run is how an
  unverified claim ships.
- **Measure viewer memory as an A/B inside ONE viewer lifetime.** Settled-idle
  `AnthiasViewer` RSS varies enormously between restarts on the same board with
  the same image and an empty playlist (86.9 / 199 / 209 MB observed on the Pi 2,
  depending on whether splash-page memory was still resident). A
  restart-to-restart before/after comparison is therefore meaningless. Drive both
  cases through one running viewer and sample the phase label alongside memory so
  each number is attributable.
- **`memory.peak` is fd-local, and that silently corrupts results.** Linux's
  resettable cgroup peak resets per open file descriptor, so a `cat` issued after
  a `tee`-based reset returns the *since-boot* peak, not the peak since your
  reset. A naive reset-then-read reports garbage that looks plausible. Either hold
  one fd across reset and read, or sample `memory.current` on an interval and take
  the maximum yourself.

## 6. Per-board memory / hardware quirks

- **On the 1 GB Rock Pi 4 it is celery, not the viewer, that exhausts swap.**
  This bullet previously claimed the board "could not settle the latest viewer
  even with an empty playlist" and blamed the Chromium/QWebEngine baseline. That
  was measured on a board whose playlist was **not** actually empty — four assets
  were still enabled, including a webpage and a streaming asset, left over from an
  earlier run (see the "disable idle testbeds' assets" rule in §5, which is
  exactly the trap). Re-measured with the playlist genuinely disabled:
  * viewer alone (201 MB RSS + 188 MB QtWebEngine, under its ~773 MB cgroup cap)
    sits comfortably; the board holds at ~173 MB available.
  * bringing celery up on top drives swap to zero on **both** the release build
    and the previous baseline — i.e. it is the steady state of this board, not a
    regression in any given build.
  * with celery stopped (§6's sanctioned mitigation) it is stable at 390-405 MB
    available and 368-396 MB swap free.
  So: read a wedge here as *total* memory pressure and suspect celery + leftover
  enabled assets first. **Verify the playlist is really disabled before quoting
  any idle memory number** — an "idle" measurement on a board that is quietly
  rendering a webpage is worthless. A genuine wedge (SSH banner-exchange timeout
  while ICMP and TCP/22 stay up) still needs a physical power cycle.
- **Read any "wedge / drop / SSH unresponsive" on a ≤1 GB board as memory
  pressure first, not CPU.** To exercise an arm64 code path without the RAM
  ceiling, override `DEVICE_TYPE=arm64` on a stable board (Pi 4 / Pi 5) instead.
- **A self-sustaining wedge won't survive a power cycle either.** If the wedge is
  caused by an *enabled* asset (a 1080p video on a 512 MB board), rebooting just
  replays it: the board boots, the viewer loads the same asset, and it wedges
  again. Break the loop instead — race a `docker stop anthias-anthias-viewer-1`
  against the boot (poll SSH in a tight loop and fire the moment it answers),
  then disable the asset before restarting the viewer.
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
