# shellcheck shell=bash
#
# bin/lib/viewer/platform_wayland.sh — cage (wlroots) display bring-up
# and launch for x86 / arm64 / pi5, sourced by bin/start_viewer.sh.
# Function definition only. Moved verbatim from start_viewer.sh.

launch_wayland() {
    # libseat's default `logind` backend D-Buses into systemd-logind to
    # acquire a session, but containers have no logind session — cage
    # exits with "Could not get primary session for user". Switch to
    # the `builtin` direct-device backend; the viewer container runs
    # privileged so /dev/dri and /dev/input are open to it.
    # WLR_LIBINPUT_NO_DEVICES=1 lets wlroots start without input
    # devices — a digital-signage kiosk has no keyboard or mouse.
    export LIBSEAT_BACKEND=builtin
    export WLR_LIBINPUT_NO_DEVICES=1

    # x86 (Intel i915 in particular) can wedge cage's display pipeline
    # under heavy QtWebEngine GPU load (issue #2976): wlroots 0.18's
    # non-blocking atomic page-flip commit gets EBUSY from the kernel
    # while the previous commit is still fence-blocked behind the web
    # render, and the trixie cage 0.2 / wlroots 0.18 failure path
    # doesn't always recover — the screen freezes on the last frame
    # until the container restarts, even though the viewer keeps
    # cycling assets. The legacy KMS interface (drmModePageFlip) has
    # no shallow-queue EBUSY semantics, so it tolerates GPU fence
    # backpressure; a single-output kiosk uses none of the atomic
    # extras (VRR, overlay planes, multi-output modeset queueing).
    # WLR_DRM_NO_MODIFIERS pairs with it: the same trixie wlroots
    # 0.18.2 + i915 stack has a second documented freeze in the
    # modifier-fallback swapchain realloc (Debian bug 1112158), and
    # legacy flips reject scan-out buffers whose modifiers change
    # between frames — modifier-less allocation keeps the legacy path
    # maximally reliable. ${VAR:-1} keeps both user-overridable via a
    # compose override (wlroots parses 0 as false). Scoped to x86:
    # arm64/pi5 run vc4/rockchip where atomic is the well-exercised
    # path and no freeze has been observed.
    if [ "$DEVICE_TYPE" = 'x86' ]; then
        export WLR_DRM_NO_ATOMIC="${WLR_DRM_NO_ATOMIC:-1}"
        export WLR_DRM_NO_MODIFIERS="${WLR_DRM_NO_MODIFIERS:-1}"
    fi

    # cage default `-m extend` spans all enumerated DRM outputs,
    # including ones that are physically disconnected — so a Pi user
    # who plugs into the second micro-HDMI port (HDMI-A-2 instead of
    # HDMI-A-1) ends up with cage rendering to a portion of the
    # virtual canvas that lands on the disconnected connector, and a
    # black screen. Trixie ships cage 0.1.x which has no `-o
    # <connector>` flag, but `-m last` restricts output to whichever
    # connector came up most recently — for the boot-time case
    # (which the kernel detects in enumeration order) that's the
    # last connected output rather than the first. Good enough for
    # the single-display kiosk path; dual-head signage is a separate
    # workflow.
    cage_mode=(-m last)

    # Self-heal a headless-boot display wedge on cage (Pi 5 vc4 in
    # particular). When the container restarts *from* a state where cage
    # was running with no output — it booted headless, then the display
    # was hotplugged but cage, with no udev in-container, never picked it
    # up, so the viewer's output watchdog exited to force this restart —
    # the restart-policy path relaunches cage almost immediately. The old
    # cage is SIGKILLed as the old PID 1 dies, and if its DRM-master
    # release and the vc4 HDMI connector reset haven't finished when the
    # fresh cage opens the device, cage's modeset races a half-reset
    # connector: EDID isn't re-read, the connector reads "connected" with
    # an EMPTY mode list, and cage comes up headless *again*. It's a
    # race, so it only bites some restarts — but every time it does the
    # screen stays black. (A full `docker restart` recovers reliably
    # precisely because its stop->start gap gives the controller time to
    # reset; the restart policy inserts no such gap.)
    #
    # Reproduce that gap here, before cage launches, so cage starts
    # against a settled connector. Two separate mechanisms:
    #   * the settle sleep gives the controller time to reset and is what
    #     actually fixes the race — but only matters on a restart, so it
    #     runs only when a display is connected (a headless boot skips it
    #     and pays nothing);
    #   * the forced EDID re-probe (echo detect) is ONLY for the case
    #     where the fast restart dropped a connector's EDID — connected
    #     but empty modes. It is therefore gated on empty modes: a healthy
    #     connector already exposes its modes, and force-re-detecting it
    #     would needlessly re-negotiate and can flash the screen on an
    #     ordinary boot. The sysfs status node is writable from inside the
    #     privileged container. Scoped to the cage branch — this is where
    #     the vc4/wlroots wedge was reproduced.
    display_connected=0
    for status_file in /sys/class/drm/card*-*/status; do
        [ -r "$status_file" ] || continue
        [ "$(cat "$status_file" 2>/dev/null)" = 'connected' ] || continue
        display_connected=1
        break
    done
    if [ "$display_connected" = 1 ]; then
        # Settle: let any prior cage's DRM-master release + connector
        # reset drain before this cage grabs the device.
        sleep 4
        for status_file in /sys/class/drm/card*-*/status; do
            [ -r "$status_file" ] || continue
            [ "$(cat "$status_file" 2>/dev/null)" = 'connected' ] || continue
            modes_file="${status_file%/status}/modes"
            # Healthy connector already has its EDID modes — nothing to
            # restore, and forcing a re-detect could re-negotiate the mode
            # and flash the screen. Only re-probe a connected-but-modeless
            # connector, which is the fast-restart EDID-drop symptom.
            [ -n "$(cat "$modes_file" 2>/dev/null)" ] && continue
            connector=$(basename "$(dirname "$status_file")")
            echo "start_viewer: $connector connected but no EDID modes;" \
                "re-probing before launching cage"
            echo detect > "$status_file" 2>/dev/null || true
            waited=0
            while [ -z "$(cat "$modes_file" 2>/dev/null)" ] \
                && [ "$waited" -lt 5 ]; do
                sleep 1
                waited=$((waited + 1))
            done
        done
    fi

    # cage runs as root (Dockerfile's USER root) and creates the
    # Wayland socket with root:root 0600 perms, so `sudo -u viewer`
    # below can't connect (Qt: "Failed to create wl_display
    # (Permission denied)"). Chown the socket to viewer in cage's
    # child *before* dropping privileges. cage exports WAYLAND_DISPLAY
    # before exec'ing the child, so the path is fully resolved here.
    cage "${cage_mode[@]}" -- bash -c '
        chown viewer "${XDG_RUNTIME_DIR}/${WAYLAND_DISPLAY}" 2>/dev/null || true
        exec sudo \
            --preserve-env=XDG_RUNTIME_DIR,QT_SCALE_FACTOR,PYTHONPATH,WAYLAND_DISPLAY,LANG,LANGUAGE,LC_ALL \
            -E -u viewer \
            dbus-run-session /venv/bin/python -m anthias_viewer
    ' &
}
