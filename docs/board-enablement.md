# Board Enablement Test Bed

A reproducible playback test bed for validating the viewer stack across boards.
Use this when changing anything that touches mpv flags, hwdec, the cage/Wayland
stack, or `src/anthias_viewer/media_player.py`.

The viewer is tuned per-board (see `media_player.py` and `bin/start_viewer.sh`):

| Device   | Qt platform | Compositor | mpv VO                          |
|----------|-------------|-----------|---------------------------------|
| Pi 2 / 3 | Qt5 linuxfb | none       | VLC                              |
| Pi 4-64  | Qt6 linuxfb | none       | `--vo=gpu --gpu-context=drm`     |
| Pi 5     | Qt6 wayland | cage       | `--vo=gpu --gpu-context=wayland` |
| arm64    | Qt6 wayland | cage       | `--vo=gpu --gpu-context=wayland` |
| x86      | Qt6 wayland | cage       | `--vo=gpu --gpu-context=wayland` |

Each combination has different hwdec, scaling, and compositing characteristics,
so a regression on one board can hide behind a clean run on another. Run the
same asset rotation everywhere and compare drop counts.

## Goal: hardware-accelerated playback on every board

Every clip Anthias displays should decode in hardware on the target board.
Software decode produces drops, heats the SoC, and on low-end Pis can't keep up
at 1080p30, let alone 4K.

Anthias does *not* re-encode video uploads on-device — see the asset processor
docstring (`src/anthias_server/processing.py`) for why. The viewer's per-board
mpv hwdec dispatch handles every codec a modern board can decode in hardware
(H.264, HEVC, plus VAAPI's wider set on x86). For codecs the board can't decode
(MPEG-2 DVD rips, MPEG-4 ASP DivX clips, AV1 outside x86, …), playback will
visibly stutter; the asset list surfaces `metadata['video_codec']` /
`metadata['video_width']` / `metadata['video_height']` / `metadata['video_fps']`
so operators can spot a misfit clip before pushing it to the field.

The viewer (`src/anthias_viewer/media_player.py`) selects the correct
mpv hwdec per codec on the target board. On Pi 4 / Pi 5 the launcher ffprobes
the asset and passes `--hwdec=v4l2m2m-copy` for H.264 or `--hwdec=drm-copy`
for HEVC directly on the mpv command line. (An earlier attempt used a Lua
`on_load` hook, but `video-codec-name` is empty at every script event before
hwdec init, so the hook was a silent no-op and `--hwdec=auto-copy` leaked
through; `auto-copy`'s upstream whitelist excludes `v4l2m2m-copy`, so H.264 fell
back to software.)

## Hardware decode capabilities per Pi

What the SoC can do, regardless of player:

| Pi   | SoC      | H.264 HW                  | HEVC HW                | VP9 / AV1 HW |
|------|----------|---------------------------|------------------------|--------------|
| 2    | BCM2836  | yes, up to 1080p (V3D IV) | **no** — no HEVC block | no           |
| 3    | BCM2837  | yes, up to 1080p (V3D IV) | **no** — no HEVC block | no           |
| 4    | BCM2711  | yes, up to 1080p60 (V3D 6.0 V4L2 M2M); 4K H.264 is past the V3D's envelope | yes, up to 4Kp60 (dedicated HEVC block, exposed as `v4l2_request_hevc`) | no |
| 5    | BCM2712  | yes in silicon (Hantro G1), but **not reachable through mpv** — no `v4l2-request` H.264 hwdec exists upstream | yes, up to 4Kp60 (Hantro G2, exposed as `v4l2_request_hevc`) | no |

HEVC HW decode arrived with the Pi 4. Pi 2 / Pi 3 cannot decode HEVC in
hardware at all, and software HEVC on a Cortex-A53 won't even hit 1080p30 — so
uploading an HEVC asset for a Pi 2 / Pi 3 fleet member will play (badly) on the
SoC's software fallback. If you need HEVC content on that fleet, transcode it
upstream of the upload.

> **Pi 5 4K HEVC requires `dtoverlay=vc4-kms-v3d,cma-512`.** The Hantro G2
> driver allocates DMA buffers from the kernel's Contiguous Memory Allocator.
> Pi OS for Pi 5 reserves only 64 MB CMA by default (vs. 512 MB on Pi 4),
> which is enough for 1080p HEVC reference + output buffers but not 4K — at
> 4K mpv hits `v4l2_request_hevc_start_frame: Failed to get dst buffer` and
> silently SW-falls-back. Bumping `cma=512M` on the kernel cmdline does
> **not** work: the kernel takes the cmdline value over the device-tree
> `linux,cma` node, which leaves `rpi-hevc-dec` orphaned
> (`Failed to probe hardware -517`) and `/dev/video*` disappears entirely,
> killing HEVC HW at every resolution. The right fix is the
> `dtoverlay=vc4-kms-v3d,cma-512` line in `/boot/firmware/config.txt` —
> the vc4 overlay carries the `cma-N` knob and resizes the DT-declared
> region without orphaning the HEVC driver. The Anthias ansible template
> at `ansible/roles/system/templates/config.txt.j2` writes that line on
> install.

## Rock Pi 4 / arm64

`bin/install.sh` sets `DEVICE_TYPE=arm64` for every aarch64 SBC it doesn't
recognise as a Pi. `anthias_host_agent` runs on the host and reads
`/proc/device-tree/model`; when it sees "Radxa ROCK Pi 4" it writes
`host:board_subtype = 'rockpi4'` to Redis. The viewer reads that key to
upgrade its `--hwdec=` choice from the catch-all `arm64` default to the
RK3399-specific `--hwdec=drm-copy` (v4l2_request, served by `rkvdec` for HEVC
and the Hantro VPU for H.264).

The arm64 viewer image pulls `ffmpeg` and the libav* family from
`archive.raspberrypi.com` (the `+rpt1` build), which adds
`--enable-v4l2-request --enable-libudev --enable-vout-drm` — the same package
family Pi 4 / Pi 5 use, so the RK3399's stateless decoders are reachable via
the same mpv flag. The `start_viewer.sh` entrypoint creates the `/dev/video-dec*`
symlinks the v4l2_request decoder discovery code expects (privileged docker
mounts its own /dev tmpfs without udev's symlinks). The `+rpt1` repo is pinned
to only override ffmpeg + libav* + mpv on arm64; Pi userspace baseline is
unaffected on every board.

## Sample pack

Run `bin/generate_board_enablement_testbed.sh` on a workstation
(not the device under test) to produce the 8-clip pack:

```bash
bash bin/generate_board_enablement_testbed.sh ~/bbb-testbed
```

The script:

1. Downloads four Big Buck Bunny H.264 + AAC sources (public-domain,
   from `download.blender.org/demo/movies/BBB`) — skipped if already
   present.
2. Trims each to 60 seconds via `-c copy` (instant, no re-encode) —
   produces the H.264 half of the pack.
3. Re-encodes each cut with `libx265 -preset medium -crf 23 -tag:v hvc1`
   — produces the HEVC half.
4. Prints a verification table (codec + resolution + fps + duration
   from `ffprobe`).

| File                             | Codec | Resolution | fps |
|----------------------------------|-------|------------|-----|
| `bbb_1080p_30fps.mp4`            | H.264 | 1920×1080  | 30  |
| `bbb_1080p_60fps.mp4`            | H.264 | 1920×1080  | 60  |
| `bbb_4k_30fps.mp4`               | H.264 | 3840×2160  | 30  |
| `bbb_4k_60fps.mp4`               | H.264 | 3840×2160  | 60  |
| `bbb_1080p_30fps_hevc.mp4`       | HEVC  | 1920×1080  | 30  |
| `bbb_1080p_60fps_hevc.mp4`       | HEVC  | 1920×1080  | 60  |
| `bbb_4k_30fps_hevc.mp4`          | HEVC  | 3840×2160  | 30  |
| `bbb_4k_60fps_hevc.mp4`          | HEVC  | 3840×2160  | 60  |

60 seconds per clip is enough to capture mpv's `hwdec-current` banner and
read a stable `Dropped:` count, while keeping a full pack regen achievable
in a few minutes on a laptop. Pass `CUT_SECONDS=N` to the script to change
the per-clip length; pass `HEVC_CRF=N` to override the encoder's quality
target.

The script is idempotent: clips that already exist (and pass an `ffprobe`
sanity check) are skipped on re-run. A power cycle mid-encode leaves the
temp file as `*.tmp.mp4`; the next invocation regenerates from scratch.

## The rotation

Upload all eight files (4 × H.264 + 4 × HEVC) as Anthias assets and schedule
them back-to-back. Per-asset boundaries in the drop log make it easy to slice
results by resolution / fps / codec.

## Drop logging

Set `ANTHIAS_DEBUG_DROPS=1` on the `anthias-viewer` service (compose override
or `~/anthias/.env`). When enabled, `media_player.py`:

- drops mpv's `--no-terminal`, so the status line
  (`AV: 00:00:30 / ... Dropped: N`) is emitted continuously;
- redirects stdout/stderr to `/data/.anthias/mpv.log` inside the container,
  which is `~/.anthias/mpv.log` on the host (or `~/.screenly/mpv.log` on
  pre-rebrand installs);
- writes a `--- mpv launch <uri> ---` marker before each mpv launch so the
  log can be sliced per asset;
- captures mpv's `hwdec-current` and VO init banners on stderr — confirms
  `--vo=gpu --gpu-context=drm` (Pi 4) vs `--gpu-context=wayland` (Pi 5 / x86
  / arm64) actually took effect, and confirms which hwdec the per-codec
  dispatch selected.

With the env var unset, the viewer keeps its silent `DEVNULL` behaviour —
no host-side log file.

## Reading the log

`Dropped:` in mpv's status line is cumulative for a single mpv process, and
the viewer spawns one process per asset, so the last `Dropped:` before the
next `--- mpv launch` marker is that asset's final count over its playback
window.

```bash
grep -E "^--- mpv launch|Dropped:" ~/.anthias/mpv.log | tail -80
```

For a single rolling sample on a running device:

```bash
tail -F ~/.anthias/mpv.log | grep --line-buffered -E "launch|Dropped:|hwdec-current|VO:"
```

## Reporting

When attaching results to a PR, include:

- board + Qt/compositor combination (one row of the table above);
- one drop count per asset, taken from the last `Dropped:N` of each asset's
  window;
- the matching `VO:` / `hwdec-current` banner lines so the run can be tied to
  a specific stack.

For comparable numbers, let the rotation play at least two full cycles before
sampling — first cycle includes asset-cache warmup and webview teardown.
