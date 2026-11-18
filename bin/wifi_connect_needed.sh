#!/bin/sh
# Decide whether the WiFi setup portal is needed on this boot.
#
# Exit 0  -> this device has no network and no WiFi configured; raise
#            the portal.
# Exit 1  -> leave it alone (systemd ExecCondition treats a clean
#            non-zero as "skip", not as a failure).
#
# The conditions below are the union of the two implementations that
# came before, because each one on its own is wrong here.
#
#   Anthias's own, pre-2026.04 (bin/start_wifi_connect.sh): an internet
#   probe, `wget --spider 1.1.1.1`.
#
#   balena's (scripts/start.sh): `iwgetid -r` — is there an active WiFi
#   connection. Their script ships a menu of four conditions and this
#   is the one they picked; notably `nmcli -t g | grep full` is in that
#   list, commented out.
#
# Both deliberately check something they own rather than trusting
# NetworkManager's connectivity verdict, and they are right to: NM is
# built WITHOUT connectivity-check support on every platform Anthias
# supports, so it answers "unknown" forever. Verified across the whole
# QA fleet — Pi 5, Pi 4, Pi 3 B+, Rock Pi 4, x86.
#
# Why the union and not just one of them:
#
#   An internet probe alone is too broad. A device whose WAN is down
#   but whose WiFi is perfectly associated would fail it, and "fixing"
#   that by raising an access point tears down the working WiFi to
#   offer to configure WiFi. The outage becomes ours.
#
#   A WiFi-association check alone is too narrow. An Ethernet-only
#   screen has no association and never will, so it would be offered
#   WiFi setup on every check forever.
#
# So: skip if WiFi is already set up, skip if the device has a working
# wired network, skip if it can reach the internet by any route. Raise
# the portal only when none of that holds — which is the first-boot
# case the portal exists for. Every condition errs towards NOT raising
# an access point, which is the safe direction.
set -u

# wifi-connect's own AP profile (AP_PROFILE_ID upstream). While the
# portal is up, wlan0 reads `connected` against this profile — without
# excluding it, a running portal would look like "WiFi is configured"
# and a retry would skip.
AP_PROFILE_ID="wifi-connect-portal"

# Same probe target as anthias_host_agent. Public anycast resolver used
# purely as a reachability check. NOSONAR (S1313).
PROBE_URL="https://1.1.1.1"  # NOSONAR

# A boot can reach this point before DHCP or WiFi association has
# finished, even behind NetworkManager-wait-online. Re-check rather
# than declaring a device unconfigured because we asked early — a
# false "needs setup" costs a needless access point, which is the
# whole thing this guards against.
#
# 6 attempts with 5s between them is a 25-second window, not 30: the
# sleep falls BETWEEN attempts, so there are five of them, and the
# probe timeout adds to that only when it actually times out.
ATTEMPTS=6
INTERVAL=5

# The container the unit starts. Checked because it can legitimately be
# missing: upgrade_containers.sh pre-creates it best-effort, so a device
# that could not reach ghcr.io -- an airgapped site, a registry outage --
# has a compose file naming a container that does not exist. Without this
# check the gate says "raise", ExecStart fails with "No such container",
# and the unit lands in `systemctl --failed` every five minutes forever.
CONTAINER=anthias-wifi-connect

# How many times to raise the portal at the timer's own 5-minute cadence
# before slowing down, and how long to wait between attempts after that.
#
# The portal is worth retrying promptly: a first boot is somebody standing
# in front of the screen with a phone, and three goes covers the window
# where they walk off to find the password. After that the odds that the
# next five-minute attempt is the one that works are small, and the cost
# is not small -- the viewer shows the setup screen for the whole 15
# minutes the portal is up (ACTIVITY_TIMEOUT), so an unattended device
# with nothing to join spends three quarters of its life showing a setup
# page instead of the content it was installed to play. An airgapped site
# is the clearest case: there may be no network to join at all, and before
# the backoff that device never played anything again.
#
# Six hours rather than "give up": nothing here is a permanent verdict. A
# network can appear, someone can plug in ethernet, an AP can come back,
# and a device that stopped asking forever would need a site visit to
# notice. Four quiet attempts a day costs 1 minute in 24 of screen time.
#
# State lives in the unit's RuntimeDirectory, so a reboot resets it: power
# cycling is what an operator does when they want the thing to try again,
# and it would be rude to ignore that. To reset without rebooting, delete
# the file and start the unit.
FAST_RAISES=3
COOL_OFF_S=21600
STATE_DIR="${RUNTIME_DIRECTORY:-/run/${CONTAINER}}"
STATE_FILE="${STATE_DIR}/raises"

wifi_is_associated() {
    nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device 2>/dev/null \
        | awk -F: -v ap="${AP_PROFILE_ID}" \
            '$2 == "wifi" && $3 == "connected" && $4 != ap { found = 1 }
             END { exit !found }'
    return $?
}

wired_is_connected() {
    # Docker's veths are type ethernet but state `unmanaged`, and its
    # bridges are type bridge, so neither matches.
    nmcli -t -f DEVICE,TYPE,STATE device 2>/dev/null \
        | awk -F: '$2 == "ethernet" && $3 == "connected" { found = 1 }
                   END { exit !found }'
    return $?
}

internet_is_reachable() {
    curl -fsS --max-time 5 -o /dev/null "${PROBE_URL}" 2>/dev/null
    return $?
}

container_exists() {
    docker container inspect "${CONTAINER}" >/dev/null 2>&1
    return $?
}

# Is any interface still making up its mind?
#
# The retry window above exists for a boot that gets here before DHCP or
# association has finished. When nothing is in flight there is nothing to
# wait for, and sleeping out the full window anyway costs 25 seconds on
# every timer tick of every offline device -- and delays the portal by 25
# seconds on the genuine first boot this is all for.
#
# `unavailable` counts as in flight for a WIFI device only: that is what
# a radio reads as while its driver comes up. On an ethernet port it is
# what "no cable" looks like, which is a settled state and the normal one
# on exactly the devices this gate is for — treating it as transitional
# made every WiFi-only screen sleep out the full window on every tick.
settling() {
    nmcli -t -f DEVICE,TYPE,STATE device 2>/dev/null \
        | awk -F: '$3 ~ /^(connecting|configuring|ip-config|prepare)/ { found = 1 }
                   $2 == "wifi" && $3 ~ /^unavailable/ { found = 1 }
                   END { exit !found }'
    return $?
}

# Forget the attempt history: the device is online, so whatever we were
# retrying is moot and the next genuine outage starts from a clean slate.
clear_raises() {
    rm -f "${STATE_FILE}" 2>/dev/null || true
    return 0
}

i=1
while [ "${i}" -le "${ATTEMPTS}" ]; do
    if wifi_is_associated; then
        echo "WiFi is already configured and associated; skipping the" \
             "setup portal"
        clear_raises
        exit 1
    fi
    if wired_is_connected; then
        echo "a wired network is connected; skipping the setup portal" \
             "(configure WiFi from the web UI or nmtui if you want it)"
        clear_raises
        exit 1
    fi
    if internet_is_reachable; then
        echo "the device can reach the internet; skipping the setup portal"
        clear_raises
        exit 1
    fi
    if ! settling; then
        break
    fi
    i=$((i + 1))
    [ "${i}" -le "${ATTEMPTS}" ] && sleep "${INTERVAL}"
done

if ! container_exists; then
    echo "the ${CONTAINER} container does not exist, so there is nothing" \
         "to start -- its image was probably never pulled. Skipping." \
         "Re-run bin/upgrade_containers.sh once the device can reach the" \
         "registry."
    exit 1
fi

raises=0
last=0
if [ -r "${STATE_FILE}" ]; then
    read -r raises last < "${STATE_FILE}" 2>/dev/null || true
    # A truncated or hand-edited file must not wedge the gate either way.
    # The default arm is the good case: all digits, keep what was read.
    case "${raises}" in
        ''|*[!0-9]*) raises=0 ;;
        *) ;;
    esac
    case "${last}" in
        ''|*[!0-9]*) last=0 ;;
        *) ;;
    esac
fi

now="$(date +%s)"
if [ "${raises}" -ge "${FAST_RAISES}" ] && \
   [ "$((now - last))" -lt "${COOL_OFF_S}" ]; then
    echo "the portal has been raised ${raises} times with no join; waiting" \
         "$(( (COOL_OFF_S - (now - last)) / 60 )) more minutes before" \
         "trying again (reboot, or delete ${STATE_FILE}, to retry now)"
    exit 1
fi

if ! mkdir -p "${STATE_DIR}" 2>/dev/null ||
   ! printf '%s %s\n' "$((raises + 1))" "${now}" > "${STATE_FILE}" 2>/dev/null; then
    # Raise anyway: a device that cannot record attempts is still a device
    # that needs setup, and refusing here would turn an unwritable
    # directory into "WiFi setup never works".
    echo "could not record the attempt in ${STATE_FILE}; the backoff will" \
         "not apply to this run"
fi

echo "no WiFi association, no wired network and no internet; the WiFi" \
     "setup portal is needed (attempt $((raises + 1)))"
exit 0
