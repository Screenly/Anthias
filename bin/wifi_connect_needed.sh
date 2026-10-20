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

wifi_is_associated() {
    nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device 2>/dev/null \
        | awk -F: -v ap="${AP_PROFILE_ID}" \
            '$2 == "wifi" && $3 == "connected" && $4 != ap { found = 1 }
             END { exit !found }'
}

wired_is_connected() {
    # Docker's veths are type ethernet but state `unmanaged`, and its
    # bridges are type bridge, so neither matches.
    nmcli -t -f DEVICE,TYPE,STATE device 2>/dev/null \
        | awk -F: '$2 == "ethernet" && $3 == "connected" { found = 1 }
                   END { exit !found }'
}

internet_is_reachable() {
    curl -fsS --max-time 5 -o /dev/null "${PROBE_URL}" 2>/dev/null
}

i=1
while [ "${i}" -le "${ATTEMPTS}" ]; do
    if wifi_is_associated; then
        echo "WiFi is already configured and associated; skipping the" \
             "setup portal"
        exit 1
    fi
    if wired_is_connected; then
        echo "a wired network is connected; skipping the setup portal" \
             "(configure WiFi from the web UI or nmtui if you want it)"
        exit 1
    fi
    if internet_is_reachable; then
        echo "the device can reach the internet; skipping the setup portal"
        exit 1
    fi
    i=$((i + 1))
    [ "${i}" -le "${ATTEMPTS}" ] && sleep "${INTERVAL}"
done

echo "no WiFi association, no wired network and no internet after" \
     "${ATTEMPTS} checks; the WiFi setup portal is needed"
exit 0
