# shellcheck shell=bash
#
# bin/lib/viewer/platform_eglfs.sh — Qt eglfs_kms display bring-up
# (pi4-64 / pi3-64), sourced by bin/start_viewer.sh. Function definitions
# only; each self-guards on QT_QPA_PLATFORM / DEVICE_TYPE so it is a
# no-op on other boards. Moved verbatim from start_viewer.sh.

# Pi 4 (and any future eglfs_kms board) aborts at startup with "no
# screens available" when no display is attached: under full KMS a DRM
# connector only reads "connected" while a panel is present, so Qt's
# eglfs plugin finds no usable screen and exits — before the viewer emits
# its D-Bus handshake, so the container crash-loops on a headless or
# not-yet-negotiated board. This is the eglfs analogue of the linuxfb
# /dev/fb0 wait below; eglfs has no /dev/fb0, so gate on connector status
# instead. Wait here, before the KMS-card detection, so the detection
# sees the connected connector once a display appears; a genuinely
# headless device idles quietly and self-heals on hotplug.
eglfs_has_display() {
    local status_file connector
    for status_file in /sys/class/drm/card*-*/status; do
        [ -r "$status_file" ] || continue
        connector=$(basename "$(dirname "$status_file")")  # e.g. card0-HDMI-A-1
        # Skip virtual / non-display connectors. The KMS "Writeback"
        # connector is not a real output and always reports "unknown";
        # counting it as a display defeats the headless wait and sends
        # eglfs into a "no screens available" crash-loop on a screenless
        # board. balenaOS 2026.x exposes card0-Writeback-1, which is what
        # broke this on Pi 4 (both HDMI ports disconnected, yet the
        # writeback connector kept the guard from ever waiting).
        case "$connector" in
            *[Ww]riteback*) continue ;;
        esac
        # Treat "connected" — and the occasional bridge that reports
        # "unknown" — as present; only an all-"disconnected" board waits.
        case "$(cat "$status_file" 2>/dev/null)" in
            disconnected | '') ;;
            *) return 0 ;;
        esac
    done
    return 1
}
wait_for_eglfs_display() {
    [ "${QT_QPA_PLATFORM:-}" = 'eglfs' ] || return 0
    eglfs_has_display && return 0

    echo "start_viewer: no display connected yet — waiting. Connect or" \
        "power on the screen; the viewer starts automatically once one is present."
    local waited=0
    until eglfs_has_display; do
        sleep 5
        waited=$((waited + 5))
        if [ "$((waited % 60))" -eq 0 ]; then
            echo "start_viewer: still no display connected after ${waited}s; waiting for a display."
        fi
    done
    echo "start_viewer: display connected after ${waited}s — starting the viewer."
}

detect_eglfs_kms_card() {
# Pi 4 / Pi 3-64 render through Qt's eglfs_kms platform (see
# Dockerfile.viewer.j2), whose JSON config pins the DRM card device. The
# vc4-drm (display) and v3d (render-only) nodes race during probe, so the
# *display* card is /dev/dri/card1 on some boots/images and /dev/dri/card0
# on others — the v3d node carries no connectors. A hardcoded device
# (issue #2947) points eglfs at the render-only node on the boots where
# vc4 loses the race; Qt then finds no connectors, never takes DRM master,
# and the device hangs on the balena splash forever. Detect the card that
# actually owns connectors at runtime and rewrite the device path before
# launch. Both Qt6 Pi boards share this; pi3-64 is the 64-bit Pi 3 stream.
if { [ "$DEVICE_TYPE" = "pi4-64" ] || [ "$DEVICE_TYPE" = "pi3-64" ]; } \
    && [ -n "${QT_QPA_EGLFS_KMS_CONFIG:-}" ]; then
    kms_card=""
    # Prefer a card with a *connected* connector; otherwise fall back to
    # any card that exposes connectors at all. The render-only v3d node
    # has no `cardN-<connector>` entries, so this excludes it even on a
    # headless boot where nothing reads as "connected".
    for status_file in /sys/class/drm/card*-*/status; do
        [ -r "$status_file" ] || continue
        connector=$(basename "$(dirname "$status_file")")  # e.g. card1-HDMI-A-1
        card="${connector%%-*}"                            # e.g. card1
        [ -e "/dev/dri/$card" ] || continue
        [ -n "$kms_card" ] || kms_card="$card"
        if [ "$(cat "$status_file" 2>/dev/null)" = "connected" ]; then
            kms_card="$card"
            break
        fi
    done
    if [ -n "$kms_card" ]; then
        echo "start_viewer: eglfs DRM device = /dev/dri/$kms_card"
        # Connector names stay HDMI1/HDMI2 — Qt derives those from the
        # connector type + type-id (the `-N` suffix in sysfs), which is
        # stable on Pi 4 regardless of which card number vc4 landed on.
        cat > "$QT_QPA_EGLFS_KMS_CONFIG" <<EOF
{
  "device": "/dev/dri/$kms_card",
  "hwcursor": false,
  "pbuffers": true,
  "outputs": [
    { "name": "HDMI1", "mode": "1920x1080" },
    { "name": "HDMI2", "mode": "1920x1080" }
  ]
}
EOF
    fi
fi
}
