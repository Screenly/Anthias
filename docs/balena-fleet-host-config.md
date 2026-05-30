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
firmware-KMS (`vc4-fkms-v3d`), or if the overlay value is malformed (e.g. stray
quotes that the firmware can't parse), the display never comes up and the device
hangs on the boot splash. Codifying the overlay here is what keeps that value
correct and identical across the fleet — exactly the kind of dashboard typo that
caused [#2947](https://github.com/Screenly/Anthias/issues/2947).

## Settings reference

Why each key is set the way it is in `balena-host-config.json`:

| Key | Boards | Rationale |
| --- | ------ | --------- |
| `dtoverlay=vc4-kms-v3d` | pi2, pi3, pi4-64 | Full-KMS driver required by the viewer's display stack (see above). |
| `dtoverlay=vc4-kms-v3d,cma-512` | pi5 | Full KMS plus a 512 MB CMA pool, which `docs/board-enablement.md` documents as required for 4K HEVC hardware decode. |
| `dtparam=i2c_arm=on,spi=on,audio=on` | pi4-64 | Enable the I²C/SPI buses and onboard audio. |
| `gpu_mem` (128 / 256) | pi2, pi3, pi4-64 | Backs the VideoCore hardware video decoders (`bcm2835-codec` / V4L2 M2M). **Not set on pi5** — the BCM2712 has no firmware memory split and ignores `gpu_mem` (it is CMA-only). |
| `disable_overscan=1` | pi2, pi3, pi4-64 | Drop the default overscan border so the image fills the panel. |
| `framebuffer_depth=32`, `framebuffer_ignore_alpha=1` | pi2, pi3, pi4-64 | Legacy firmware-framebuffer hints. Inert under full KMS but retained to avoid changing long-standing fleet config; safe to drop if the fleets are ever re-baselined. |
| _(none)_ | x86 | `config.txt` is Raspberry-Pi-only; the x86 fleet has no host config. |

## Editing

Edit `balena-host-config.json` and let the next release build reconcile, or for
an out-of-band change use `balena env set` / `balena env rm` directly and mirror
it back into the file so the two stay in sync.

> The manual, per-setting instructions at
> <https://anthias.screenly.io/docs/balena/> remain the path for
> **self-hosted** fleets, which this pipeline does not manage.
