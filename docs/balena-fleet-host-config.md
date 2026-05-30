# Balena fleet host configuration (IAC)

Anthias's official balena fleets (`screenly_ose/anthias-{pi2,pi3,pi4,pi5,x86}`)
need a handful of `config.txt`-level device settings — most importantly the
graphics driver overlay. These used to be set by hand in the balena dashboard,
which drifted per fleet and was easy to corrupt. They are now declared as code
in [`balena-host-config.json`](../balena-host-config.json) and applied by the
`Apply fleet host configuration` step in
[`.github/workflows/build-balena-disk-image.yaml`](../.github/workflows/build-balena-disk-image.yaml).

## How it works

On each release build, per board, the workflow:

1. **Upserts** every key in `balena-host-config.json` under the canonical
   `BALENA_HOST_CONFIG_<key>` name via `balena env set` (idempotent).
2. **Prunes** any fleet *config* variable not listed in the file — including
   legacy `RESIN_HOST_CONFIG_*` duplicates — so the file is authoritative.

Only `*_HOST_CONFIG_*` variables are touched. Supervisor variables
(`RESIN_SUPERVISOR_*`) are platform settings, not `config.txt`, and are left
alone.

> Note: a freshly flashed device boots once on stock balenaOS defaults, then
> the supervisor fetches the fleet's target state, rewrites `config.txt`, and
> reboots into the configured graphics stack. This requires connectivity on
> first boot.

## Why `dtoverlay=vc4-kms-v3d` matters

Since [#2905](https://github.com/Screenly/Anthias/pull/2905) the Pi 4 viewer
renders through Qt's `eglfs_kms` platform (and Pi 5 / x86 through `cage` /
wlroots). Both require the **full-KMS** atomic driver `vc4-kms-v3d`. Under
firmware-KMS (`vc4-fkms-v3d`) or a missing/unparseable overlay, the display
never comes up and the device hangs on the boot splash. Before #2905 the Pi 4
used `linuxfb`, which worked over the firmware framebuffer and masked this
dependency.

## Audit (2026-05-30)

State pulled from the live fleets with
`balena env list --fleet <slug> --config --json`.

| Board   | Setting (live)                                   | Verdict | Action |
| ------- | ------------------------------------------------ | ------- | ------ |
| pi4-64  | `dtoverlay="vc4-kms-v3d"` (RESIN_, **quoted**)   | **Broken** — stray quotes stop the overlay loading → fkms fallback → eglfs can't start → boot-splash hang ([#2947](https://github.com/Screenly/Anthias/issues/2947)) | Fixed: clean `vc4-kms-v3d`, BALENA_ prefix |
| pi4-64  | `dtparam=...,"vc4-kms-v3d"`                       | **Bug** — `vc4-kms-v3d` is an overlay, not a dtparam | Dropped; kept `i2c_arm=on,spi=on,audio=on` |
| pi2/pi3 | `dtoverlay=vc4-kms-v3d` on `RESIN_` prefix        | Works, legacy prefix | Standardized to `BALENA_` |
| pi5     | `gpu_mem=1024`                                    | **No-op** — Pi 5 (BCM2712) has no firmware memory split; CMA-only | Dropped |
| pi5     | `dtoverlay=vc4-kms-v3d` (no `cma`)                | Suboptimal — `docs/board-enablement.md` documents `cma-512` as required for 4K HEVC HW decode | Changed to `vc4-kms-v3d,cma-512` |
| pi2/3/4 | `framebuffer_depth=32`, `framebuffer_ignore_alpha=1` | Inert — legacy firmware-framebuffer knobs ignored by full KMS | Retained (harmless), not churned |
| ≤ pi4   | `gpu_mem` (128 / 256)                             | Useful — backs the VideoCore HW video decoders (bcm2835-codec / V4L2 M2M) | Retained |
| x86     | (none)                                            | Correct — `config.txt` is Pi-only | — |

The Pi 4 corruption was repaired on the live fleet immediately; every other
change above is encoded in `balena-host-config.json` and converges on the next
release build.

## Editing

Edit `balena-host-config.json` and let the next release build reconcile, or for
an out-of-band change use `balena env set` / `balena env rm` directly and mirror
it back into the file so the two stay in sync.

> The manual, per-setting instructions at
> <https://anthias.screenly.io/docs/balena/> remain the path for
> **self-hosted** fleets, which this pipeline does not manage.
