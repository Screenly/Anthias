#!/bin/sh
# Decide whether the WiFi setup portal is needed on this boot.
#
# Exit 0  -> the device cannot reach the internet; raise the portal.
# Exit 1  -> the device is online; skip it (systemd ExecCondition
#            treats a clean non-zero as "skip", not as a failure).
#
# Why Anthias makes this call instead of wifi-connect
# ---------------------------------------------------
# wifi-connect asks NetworkManager whether the device is online, and
# treats "I don't know" as a reason to show the portal — a sensible
# default for a tool that assumes NM can answer. On every platform
# Anthias supports, NM cannot: Raspberry Pi OS and Debian both ship it
# built WITHOUT connectivity-check support, so
# `ConnectivityCheckAvailable` is false and the honest answer is always
# "unknown". Verified across the whole QA fleet — Pi 5, Pi 4, Pi 3 B+,
# Rock Pi 4 and x86, every one of them false.
#
# Without this gate the portal therefore comes up on every boot of
# every device, online or not. On a device that is online over WiFi
# that is worse than cosmetic: raising the access point takes the
# radio out of station mode and drops the network it was using.
#
# Measured on the Pi 5 testbed: `nmcli networking connectivity` reports
# `full`, a forced re-check reports `full`, and the portal came up
# anyway, twice out of two runs.
set -u

# Same probe target as anthias_host_agent — Cloudflare's public DNS
# anycast, used purely as a reachability check. NOSONAR (S5332): the
# scheme here is https and the address is public anycast, not internal.
PROBE_URL="https://1.1.1.1"

# A boot can reach this point before DHCP has finished, even behind
# NetworkManager-wait-online. Retry across ~30s rather than declaring a
# device offline because we asked early — a false "offline" costs a
# needless access point, which is the whole thing this guards against.
ATTEMPTS=6
INTERVAL=5

i=1
while [ "${i}" -le "${ATTEMPTS}" ]; do
    if curl -fsS --max-time 5 -o /dev/null "${PROBE_URL}" 2>/dev/null; then
        echo "online (probe succeeded on attempt ${i}); skipping the" \
             "WiFi setup portal"
        exit 1
    fi
    i=$((i + 1))
    [ "${i}" -le "${ATTEMPTS}" ] && sleep "${INTERVAL}"
done

echo "no internet after ${ATTEMPTS} attempts; the WiFi setup portal is needed"
exit 0
