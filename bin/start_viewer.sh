#!/bin/bash
#
# start_viewer.sh — orchestrates the viewer boot. Shared setup runs on
# every board; display bring-up and the actual launch are per-platform
# and live in bin/lib/viewer/*.sh (cage/wayland, eglfs, linuxfb). This
# file only wires them together in the order the boot sequence requires;
# see each lib for the reasoning behind a given step.

VIEWER_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/lib/viewer" && pwd)"
# shellcheck source=bin/lib/viewer/common.sh
source "$VIEWER_LIB_DIR/common.sh"
# shellcheck source=bin/lib/viewer/platform_wayland.sh
source "$VIEWER_LIB_DIR/platform_wayland.sh"
# shellcheck source=bin/lib/viewer/platform_eglfs.sh
source "$VIEWER_LIB_DIR/platform_eglfs.sh"
# shellcheck source=bin/lib/viewer/platform_linuxfb.sh
source "$VIEWER_LIB_DIR/platform_linuxfb.sh"

# --- Shared setup (all boards) ---
viewer_migrate_and_fix_devices
viewer_prepare_data_dirs
start_pulseaudio
viewer_clean_locale_env
viewer_prepare_runtime
viewer_detect_scale_factor
viewer_setup_render_group

# --- Display bring-up. Each step self-guards on QT_QPA_PLATFORM /
# DEVICE_TYPE, so calling them unconditionally preserves the exact boot
# order of the former monolithic script (eglfs waits + KMS detection,
# then the shared Plymouth/DRM-master handoff, then the linuxfb
# framebuffer wait). ---
wait_for_eglfs_display
detect_eglfs_kms_card
release_boot_splash
wait_for_framebuffer

# Start viewer.
# sudo resets PATH to its secure_path, so resolve python via the
# absolute venv path instead — `python` on PATH would otherwise hit
# the system interpreter, which has no Anthias deps installed.
# --preserve-env=XDG_RUNTIME_DIR forces sudo to forward the runtime dir
# we just set; -E alone is subject to env_check / env_delete and is not
# guaranteed for XDG_* on Debian's default sudoers.
#
# x86 / arm64 / pi5 run under `cage`, a kiosk wlroots compositor.
# cage acquires DRM master as root, exports WAYLAND_DISPLAY for its
# child, and exits when the child exits — so the existing kill -0
# watchdog below still works. The inner sudo drops back to the
# viewer user; WAYLAND_DISPLAY has to be added to --preserve-env to
# survive sudo's env scrub.
#
# Pi 4 (and pi3-64) fall through to the direct-sudo path (no cage)
# under QT_QPA_PLATFORM=eglfs (#2904: eglfs gives QtMultimedia a GL
# painter that linuxfb lacks). The V3D 6.0 doesn't have the bandwidth
# to composite cage on top of video at 4K (738 vo drops/30 s under
# cage vs 3-6 on the eglfs + --gpu-context=drm path), so Pi 4 stays off
# cage until either a newer mpv with v4l2request hwdec or a future Pi
# platform lets us re-evaluate; the weaker Pi 3-64 VideoCore IV stays
# off cage for the same reason. Qt5 boards (pi2/pi3) share the same
# direct-sudo fallback path under linuxfb.
case "$DEVICE_TYPE" in
    x86|arm64|pi5)
        launch_wayland
        ;;
    *)
        launch_direct_sudo
        ;;
esac

viewer_wait_and_supervise
