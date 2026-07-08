# shellcheck shell=bash
#
# bin/lib/viewer/platform_linuxfb.sh — Qt linuxfb display bring-up and
# HDMI-hotplug resolution recovery (pi2 / pi3), sourced by
# bin/start_viewer.sh. Function definitions (plus one constant) only;
# each self-guards on QT_QPA_PLATFORM so it is a no-op on other boards.
# Moved verbatim from start_viewer.sh.

# Qt's linuxfb platform (pi2/pi3) opens /dev/fb0 at startup and cannot
# recover if it is absent. Under full KMS (dtoverlay=vc4-kms-v3d) the
# framebuffer only exists while a display is connected, so a headless
# box, a powered-off panel, or a TV slow to negotiate HDMI at boot leaves
# no /dev/fb0 — and Qt doesn't fail cleanly there: it logs "Unable to
# figure out framebuffer device / no screens available" and aborts with
# heap corruption ("malloc(): unaligned tcache chunk detected"). The
# container then crash-loops, spamming the logs, and never settles.
#
# Wait for the framebuffer instead of launching into a guaranteed crash.
# No assumptions about which connector the panel is on or its resolution:
# when a display is (re)connected the KMS driver creates /dev/fb0 and we
# proceed; a genuinely headless device idles here quietly. Only the
# linuxfb path needs the /dev/fb0 wait — eglfs has no /dev/fb0 and is
# guarded by wait_for_eglfs_display above instead, while cage (wayland)
# tolerates a missing display without crashing — so the QT_QPA_PLATFORM
# guard makes this a no-op for those paths.
#
# Hotplug caveat: this (privileged) container's /dev is a devtmpfs
# snapshot taken at container start, so a display plugged in *after* we
# booted headless creates /dev/fb0 on the host but never inside our
# stale /dev — the plain `[ -e /dev/fb0 ]` wait would then block forever.
# sysfs is bind-through from the host and stays truthful, so when
# /sys/class/graphics/fb0 appears but our /dev/fb0 does not, exit and let
# the `restart: always` policy recreate us with a fresh /dev (the same
# exit-to-restart recovery the OOM and viewer-death paths below rely on).
wait_for_framebuffer() {
    [ "${QT_QPA_PLATFORM:-}" = 'linuxfb' ] || return 0
    [ -e /dev/fb0 ] && return 0

    echo "start_viewer: no framebuffer (/dev/fb0) yet — waiting for a display." \
        "Connect or power on the screen; the viewer starts automatically once one is present."
    local waited=0
    until [ -e /dev/fb0 ]; do
        sleep 5
        waited=$((waited + 5))
        # Re-check first: on a fresh container start the /dev/fb0 node can
        # lag its sysfs entry by a moment, so give it this cycle to appear
        # before treating a present sysfs entry as the stale-devtmpfs case.
        [ -e /dev/fb0 ] && break
        if [ -e /sys/class/graphics/fb0/dev ]; then
            echo "start_viewer: display present on host but /dev/fb0 missing" \
                "in container (stale devtmpfs snapshot) — exiting so the" \
                "container restarts with a fresh /dev."
            exit 1
        fi
        if [ "$((waited % 60))" -eq 0 ]; then
            echo "start_viewer: still no display after ${waited}s; waiting for a display."
        fi
    done
    echo "start_viewer: /dev/fb0 present after ${waited}s — starting the viewer."
}

# === HDMI hotplug resolution recovery (linuxfb / Pi 1-3) ===
#
# Under dtoverlay=vc4-kms-v3d the kernel's drm_fb_helper owns the
# display mode on the linuxfb boards: Qt's linuxfb plugin only paints
# /dev/fb0 and never takes DRM master, so nothing in userspace re-runs a
# modeset. When an HDMI sink on a power schedule (a TV that switches
# itself off and on) wakes up, the connector re-probe can win the race
# against the sink's EDID/DDC coming back; the connector momentarily
# reports no valid modes and drm_fb_helper latches the framebuffer to
# its hard-coded 1024x768 fallback. Qt read the framebuffer geometry
# once at startup and can't follow the change, so the picture stays
# stuck at 1024x768 until someone power-cycles the Pi (issue #3052).
#
# eglfs boards (pi4 / pi5 / pi3-64 / arm64) are immune: Qt holds DRM
# master and keeps its own modeset committed across the hotplug, so they
# hold the resolution through a TV power-cycle (verified on a Pi 3-64
# testbed: a real ~10 s HDMI unplug never left 1920x1080). The
# QT_QPA_PLATFORM guard below makes the watchdog a no-op for them.
#
# Recover by watching the HDMI connector for a disconnect->reconnect
# and, once its EDID is readable again, re-asserting the connector's
# *preferred* mode onto the framebuffer through the fbdev sysfs `mode`
# attribute. The target mode is read live from the connector, never
# hard-coded, so any panel resolution is honoured. The viewer is then
# restarted (the wait loop below exits, the container's restart policy
# brings it back) so Qt re-initialises cleanly against the restored
# mode. All reads/writes are under /sys — the viewer container is
# privileged and no DRM master is taken, so this never conflicts with
# Qt's fbdev use.
HOTPLUG_SETTLE_SECONDS="${ANTHIAS_HOTPLUG_SETTLE_SECONDS:-5}"

reassert_preferred_mode() {
    # Re-apply the connector's EDID-preferred mode to /dev/fb0.
    # $1 = connector sysfs dir. Returns non-zero (so the caller retries
    # on the next reconnect tick) while the sink hasn't published EDID.
    local conn="$1" preferred width rest height flag mode
    preferred=$(head -n1 "$conn/modes" 2>/dev/null)
    case "$preferred" in
        *x*) ;;
        *) return 1 ;;  # no modes yet — EDID still negotiating
    esac
    width=${preferred%%x*}
    rest=${preferred#*x}          # e.g. 1080 or 1080i
    height=${rest%%[!0-9]*}       # numeric height, minus any scan-type suffix
    # Preserve the scan type: interlaced sinks advertise a trailing 'i'
    # (e.g. 1920x1080i); forcing a progressive string on them is rejected.
    case "$rest" in *i*) flag='i';; *) flag='p';; esac
    # Prefer a mode string the kernel already registered for this
    # geometry — either scan type, any refresh — and only fabricate one
    # (in the fbcon "U:WxH{p,i}-<refresh>" format the vc4 DRM fbdev uses,
    # which registers a 0 refresh field) as a last resort.
    mode=$(grep -m1 -E "^U:${width}x${height}[pi]" \
        /sys/class/graphics/fb0/modes 2>/dev/null)
    [ -n "$mode" ] || mode="U:${width}x${height}${flag}-0"
    if ! echo "$mode" > /sys/class/graphics/fb0/mode 2>/dev/null; then
        # A rejected write (EDID not settled, unsupported mode) must not
        # fail silently — log it so a screen stuck at 1024x768 is
        # diagnosable. The caller retries on the next reconnect tick.
        echo "start_viewer: HDMI reconnect — could not set framebuffer" \
            "mode '${mode}' yet; will retry on the next probe."
        return 1
    fi
    echo "start_viewer: HDMI reconnect — re-asserted ${width}x${height}" \
        "framebuffer mode; restarting viewer."
    return 0
}

monitor_hdmi_resolution() {
    # linuxfb only; eglfs/wayland boards recover on their own.
    [ "${QT_QPA_PLATFORM:-}" = 'linuxfb' ] || return 0
    local viewer_pid="$1" status_file conn current
    # Watch every HDMI connector, not just one bound at startup: a device
    # may drive the second micro-HDMI port, or the sink the viewer came
    # up on may differ from where the panel ends up. Seed each
    # connector's current status so we react only to subsequent
    # disconnect->reconnect edges (a port that starts disconnected still
    # recovers the first time it connects).
    declare -A previous
    for status_file in /sys/class/drm/card*-HDMI*/status; do
        [ -r "$status_file" ] || continue
        previous["$(dirname "$status_file")"]=$(cat "$status_file" 2>/dev/null)
    done
    while kill -0 "$viewer_pid" 2>/dev/null; do
        sleep 3
        for status_file in /sys/class/drm/card*-HDMI*/status; do
            [ -r "$status_file" ] || continue
            conn=$(dirname "$status_file")
            current=$(cat "$status_file" 2>/dev/null)
            # A transient empty read (racing the connector re-probe) must
            # not overwrite the last good status, or the reconnect edge
            # gets lost — skip and re-sample next tick.
            [ -n "$current" ] || continue
            # Default an unseeded connector to 'connected' (not empty): if
            # a status file was unreadable when we seeded, an empty prior
            # would look like a reconnect edge on the first readable tick
            # and spuriously restart a display that never dropped.
            if [ "${previous[$conn]:-connected}" != 'connected' ] \
                && [ "$current" = 'connected' ]; then
                # The sink just came back. Give EDID/DDC a moment to
                # settle, then re-assert the preferred mode. On success
                # restart the viewer so Qt re-reads the framebuffer at
                # the right size; on failure (EDID not up yet) leave
                # previous[$conn] non-connected so the next loop re-checks.
                sleep "$HOTPLUG_SETTLE_SECONDS"
                if reassert_preferred_mode "$conn"; then
                    kill "$viewer_pid" 2>/dev/null
                    return 0
                fi
                continue
            fi
            previous[$conn]="$current"
        done
    done
}
