#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

# collect_debug.sh — gather everything needed to debug a faulty Anthias
# installation into a single, shareable archive.
#
# Pulls host/system info, Docker + compose state, per-container logs
# (journald-tagged in production), the Anthias config and database
# overview, an ffprobe of every video asset, network reachability,
# Redis health, storage health (filesystem error counters, card
# identity, kernel I/O errors), and Raspberry Pi specifics (power
# supply health, throttling, temperature).
#
# Device state that Anthias already computes for its own UI is captured
# by querying /api/v2/info rather than re-deriving it here, so the
# bundle tracks the API as diagnostics move into the web UI. The raw
# host-level readings remain as fallbacks for the case this script
# mostly exists for: a stack too broken to answer.
#
# A final PII-scrub pass redacts IP/MAC addresses, email addresses,
# URL-embedded credentials, the device hostname, and the secrets in
# anthias.conf from every file before the archive is built — so the
# bundle is safe to attach to a public GitHub issue or forum post.
#
# Safe to run on a live device — it is read-only and never touches the
# running stack. Output is a tarball under the user's home directory
# (or wherever --output points).
#
# Usage:
#   bin/collect_debug.sh [--output DIR] [--lines N] [--no-archive]
#
#   --output DIR     Where to write the report dir/archive (default: $HOME)
#   --lines N        Log lines to capture per container (default: 2000)
#   --no-archive     Leave the report directory in place, skip the tarball
#   -h, --help       Show this help

set -uo pipefail

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

LOG_LINES=2000
MAKE_ARCHIVE=1
OUTPUT_BASE=""

usage() {
    cat <<'EOF'
collect_debug.sh — collect a redacted debug bundle for a faulty Anthias install.

Usage:
  bin/collect_debug.sh [--output DIR] [--lines N] [--no-archive]

  --output DIR     Where to write the report dir/archive (default: $HOME)
  --lines N        Log lines to capture per container (default: 2000)
  --no-archive     Leave the report directory in place, skip the tarball
  -h, --help       Show this help

A final pass redacts IP/MAC addresses, emails, URL credentials, the
device hostname, and anthias.conf secrets from every file before the
archive is built.
EOF
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output)
            OUTPUT_BASE="${2:-}"
            shift 2
            ;;
        --lines)
            LOG_LINES="${2:-2000}"
            shift 2
            ;;
        --no-archive)
            MAKE_ARCHIVE=0
            shift
            ;;
        -h|--help)
            usage 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Environment discovery
# ---------------------------------------------------------------------------

# Resolve the operator's home dir without trusting $HOME (this may run
# under sudo, where $HOME points at /root). Mirrors migrate_legacy_paths.sh.
RUN_USER="${SUDO_USER:-${USER:-$(id -un)}}"
USER_HOME="$(getent passwd "$RUN_USER" 2>/dev/null | cut -d: -f6)"
USER_HOME="${USER_HOME:-/home/${RUN_USER}}"

# The repo / compose dir and the config dir, with legacy fallbacks.
ANTHIAS_DIR="${USER_HOME}/anthias"
[[ -d "$ANTHIAS_DIR" ]] || ANTHIAS_DIR="${USER_HOME}/screenly"

CONFIG_DIR="${USER_HOME}/.anthias"
[[ -d "$CONFIG_DIR" ]] || CONFIG_DIR="${USER_HOME}/.screenly"

COMPOSE_FILE="${ANTHIAS_DIR}/docker-compose.yml"
SSL_OVERRIDE="${ANTHIAS_DIR}/docker-compose.ssl.override.yml"

# docker compose may need sudo (the install adds the user to the docker
# group, but a freshly-installed session might not have picked it up).
DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
    if sudo -n docker info >/dev/null 2>&1; then
        DOCKER=(sudo docker)
    elif sudo docker info >/dev/null 2>&1; then
        DOCKER=(sudo docker)
    fi
fi

COMPOSE_ARGS=(compose)
if [[ -f "$COMPOSE_FILE" ]]; then
    COMPOSE_ARGS+=(-f "$COMPOSE_FILE")
    [[ -f "$SSL_OVERRIDE" ]] && COMPOSE_ARGS+=(-f "$SSL_OVERRIDE")
fi

# Production tags the containers on the journald driver; capture by tag
# as well as by container so we get logs even if the compose file moved.
CONTAINER_TAGS=(anthias-server anthias-viewer anthias-celery anthias-redis anthias-caddy)

# Captured up front so the PII-scrub pass can redact this device's
# hostname (it appears in journald prefixes, `uname -a`, and the README).
HOSTNAME_VAL="$(hostname 2>/dev/null || true)"

# ---------------------------------------------------------------------------
# Output layout
# ---------------------------------------------------------------------------

OUTPUT_BASE="${OUTPUT_BASE:-$USER_HOME}"
STAMP="$(date +%Y%m%d-%H%M%S 2>/dev/null || echo unknown)"
REPORT_NAME="anthias-debug-${STAMP}"
REPORT_DIR="${OUTPUT_BASE}/${REPORT_NAME}"
LOG_DIR="${REPORT_DIR}/logs"

mkdir -p "$LOG_DIR" || {
    echo "Could not create report directory under ${OUTPUT_BASE}" >&2
    exit 1
}

# Run a command, label it, and tee both stdout and stderr into a file.
# Never aborts the script — a missing tool just records the error.
section() {
    local title="$1" outfile="$2"
    shift 2
    {
        echo "########################################################"
        echo "# ${title}"
        echo "# \$ $*"
        echo "########################################################"
        "$@" 2>&1
        echo
    } >>"$outfile" 2>&1
}

note() { echo "  - $1"; }

# Final-pass PII redaction over every file in the report. Runs once, just
# before archiving, so it covers logs, configs, ffprobe output and the
# README alike. Order matters: MAC addresses are redacted before the
# IPv6 rule (a MAC otherwise looks like a 6-group IPv6). 127.0.0.1 and
# 0.0.0.0 are deliberately preserved — they're non-identifying constants
# that keep bind/route lines readable.
scrub_pii() {
    local f hostname_re
    while IFS= read -r -d '' f; do
        # Shield the keep-list constants from the IPv4 rule below.
        sed -i \
            -e 's/127\.0\.0\.1/__KEEP_LOOPBACK__/g' \
            -e 's/\b0\.0\.0\.0\b/__KEEP_ANY__/g' \
            "$f"

        sed -i -E \
            -e 's#(://)[^/@[:space:]]+@#\1<redacted-credentials>@#g' \
            -e 's#[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}#<redacted-email>#g' \
            -e 's#([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}#<redacted-mac>#g' \
            -e 's#([0-9A-Fa-f]{1,4}:){4,}[0-9A-Fa-f]{1,4}#<redacted-ipv6>#g' \
            -e 's#\b([0-9]{1,3}\.){3}[0-9]{1,3}\b#<redacted-ip>#g' \
            "$f"

        # Storage device serials. smartctl -i prints "Serial Number:"
        # and "LU WWN Device Id:", and the MMC/SD sysfs dump carries a
        # "serial:" line — all of them uniquely identify a physical
        # unit, which is exactly what this bundle promises not to
        # carry. Model, firmware and manufacture date are deliberately
        # kept: those are what make a support conversation useful and
        # they identify a product, not a device.
        sed -i -E \
            -e 's/^([[:space:]]*Serial Number:[[:space:]]*).*/\1<redacted-serial>/I' \
            -e 's/^([[:space:]]*LU WWN Device Id:[[:space:]]*).*/\1<redacted-wwn>/I' \
            -e 's/^([[:space:]]*serial[[:space:]]*:[[:space:]]*).*/\1<redacted-serial>/I' \
            "$f"

        sed -i \
            -e 's/__KEEP_LOOPBACK__/127.0.0.1/g' \
            -e 's/__KEEP_ANY__/0.0.0.0/g' \
            "$f"

        # The device hostname is freeform text, so redact the literal
        # value wherever it lands. Escape regex metacharacters first.
        if [[ -n "$HOSTNAME_VAL" && "${#HOSTNAME_VAL}" -ge 3 ]]; then
            hostname_re="$(printf '%s' "$HOSTNAME_VAL" | sed 's/[][\.*^$/]/\\&/g')"
            sed -i "s/${hostname_re}/<redacted-hostname>/g" "$f"
        fi
    done < <(find "$REPORT_DIR" -type f -print0)
}

echo "Collecting Anthias debug bundle..."
echo "  user        : ${RUN_USER}"
echo "  anthias dir : ${ANTHIAS_DIR}"
echo "  config dir  : ${CONFIG_DIR}"
echo "  output      : ${REPORT_DIR}"
echo

# ---------------------------------------------------------------------------
# 1. System / host
# ---------------------------------------------------------------------------

SYS="${REPORT_DIR}/system.txt"
note "system & hardware"
section "Date / uptime" "$SYS" date
section "Uptime" "$SYS" uptime
section "OS release" "$SYS" cat /etc/os-release
section "Kernel / arch" "$SYS" uname -a
section "CPU / arch detail" "$SYS" lscpu
section "Memory" "$SYS" free -h
section "Disk usage" "$SYS" df -h
section "Inode usage" "$SYS" df -ih
section "Top memory consumers" "$SYS" sh -c 'ps -eo pid,ppid,user,%cpu,%mem,rss,comm --sort=-%mem | head -20'
section "Mounts" "$SYS" mount

# Under-voltage, read from the kernel rather than the firmware mailbox.
#
# `vcgencmd get_throttled` used to be the way to check this, and it is
# still captured below, but its "has occurred since boot" bits (16-19)
# cannot be trusted: the raspberrypi-hwmon driver polls the same
# firmware property every 2 seconds and clears those sticky bits as it
# goes (it sends value = 0xffff). On every current Raspberry Pi OS and
# balenaOS Pi kernel they are wiped moments after they are set, so a
# bundle reporting 0x0 there says nothing about whether the device has
# browned out. The kernel's own rpi_volt hwmon sensor is the reliable
# reading.
#
# This is the *fallback* reading. The interpreted state, including the
# since-boot history the kernel no longer keeps, comes from
# /api/v2/info (see the "anthias api snapshot" section below), which is
# the same source the web UI renders from. This raw read exists for the
# case that makes someone run this script in the first place: a stack
# too broken to answer an HTTP request. Deliberately the whole of what
# is duplicated here, one attribute and one directory scan, rather than
# re-deriving the latch in shell.
undervoltage_sensor() {
    local found=0 name dir

    for dir in /sys/class/hwmon/hwmon*; do
        [ -r "${dir}/name" ] || continue
        name="$(cat "${dir}/name" 2>/dev/null)"
        [ "$name" = "rpi_volt" ] || continue
        found=1
        echo "sensor          : ${dir}"
        echo -n "in0_lcrit_alarm : "
        cat "${dir}/in0_lcrit_alarm" 2>/dev/null || echo "unreadable"
        echo "                  (1 = under-voltage seen within the"
        echo "                   driver's last 2-second poll)"
    done

    if [ "$found" -eq 0 ]; then
        echo "No rpi_volt hwmon sensor on this device, so under-voltage"
        echo "cannot be detected here. Expected on non-Pi hardware, or on"
        echo "a kernel built without CONFIG_SENSORS_RASPBERRYPI_HWMON."
    fi
}

# The kernel logs every brown-out, so the ring buffer carries history
# the firmware bits no longer do, and it survives an Anthias restart
# that would lose the Redis latch behind /api/v2/info. This one is
# genuinely host-only: the ring buffer is not readable from inside the
# server container, so it cannot move to the API. Matched on the
# message the driver emits ("Under-voltage detected!") plus its
# recovery counterpart.
#
# The result is captured into a variable rather than piped straight
# out: `grep | tail` exits 0 even when grep matched nothing, so a
# trailing `|| echo` would never fire and an empty section would be
# ambiguous between "no brown-outs" and "could not read dmesg".
undervoltage_kernel_log() {
    local out
    out="$({ dmesg -T 2>/dev/null || sudo -n dmesg -T 2>/dev/null \
        || dmesg 2>/dev/null; } \
        | grep -i -E 'under-?voltage|voltage normali[sz]ed' \
        | tail -50)"

    if [ -n "$out" ]; then
        echo "$out"
    else
        echo "No under-voltage messages in the kernel ring buffer."
        echo "(The buffer is finite, so this does not rule out a"
        echo " brown-out earlier in this device's uptime.)"
    fi
}

# Storage health. Same split as under-voltage: the interpreted verdict
# lives in /api/v2/info, and what follows is the raw evidence for when
# the stack is too broken to answer.
#
# ext4 keeps its error counters in the superblock rather than in
# memory, so unlike the firmware throttle bits these are durable: they
# survive reboots and are cleared only by fsck or a reformat. A
# nonzero errors_count on a device that has never been fscked is the
# single strongest piece of evidence that a card is going.
ext4_error_counters() {
    local found=0 dir name

    for dir in /sys/fs/ext4/*; do
        name="$(basename "$dir")"
        [[ "$name" = "features" ]] && continue
        [[ -r "${dir}/errors_count" ]] || continue
        found=1
        echo "filesystem      : ${name}"
        echo "errors_count    : $(cat "${dir}/errors_count" 2>/dev/null)"
        echo "first_error_time: $(cat "${dir}/first_error_time" 2>/dev/null)"
        echo "first_error_func: $(cat "${dir}/first_error_func" 2>/dev/null)"
        echo "last_error_time : $(cat "${dir}/last_error_time" 2>/dev/null)"
        echo "last_error_func : $(cat "${dir}/last_error_func" 2>/dev/null)"
        echo "last_error_block: $(cat "${dir}/last_error_block" 2>/dev/null)"
        echo "lifetime_write  : $(cat "${dir}/lifetime_write_kbytes" \
            2>/dev/null) KiB"
        echo "warning_count   : $(cat "${dir}/warning_count" 2>/dev/null)"
        echo "                  (*_error_time are epoch seconds; on a Pi"
        echo "                   with no RTC they can predate the first"
        echo "                   NTP sync and read as nonsense)"
        echo
    done

    if [[ "$found" -eq 0 ]]; then
        # NOSONAR shelldre:S7677 -- this is report content that
        # section() captures into storage.txt, not an error
        # message. Redirecting it to stderr would drop it from
        # the bundle, leaving an empty section that reads as
        # "could not check" rather than "nothing to report".
        echo "No ext4 filesystems reporting error counters."
    fi

    return 0
}

# MMC/SD identification and, on eMMC only, the wear registers. SD
# cards have no health register at all, which is why the counters
# above and the write check in the API are what this feature rests on.
mmc_devices() {
    local found=0 dev dir

    for dir in /sys/block/mmcblk*; do
        [[ -d "$dir" ]] || continue
        dev="$(basename "$dir")"
        found=1
        echo "device       : ${dev}"
        echo "type         : $(cat "${dir}/device/type" 2>/dev/null)"
        echo "name         : $(cat "${dir}/device/name" 2>/dev/null)"
        echo "manfid       : $(cat "${dir}/device/manfid" 2>/dev/null)"
        echo "oemid        : $(cat "${dir}/device/oemid" 2>/dev/null)"
        echo "date         : $(cat "${dir}/device/date" 2>/dev/null)"
        echo "fwrev        : $(cat "${dir}/device/fwrev" 2>/dev/null)"
        echo "size         : $(cat "${dir}/size" 2>/dev/null) sectors"
        # eMMC only. life_time is two 10%-band estimates (0x0b means
        # the estimate has been exceeded); pre_eol_info is 0x01
        # normal / 0x02 80% of reserve used / 0x03 90%.
        echo "life_time    : $(cat "${dir}/device/life_time" \
            2>/dev/null || echo "n/a (SD cards do not report wear)")"
        echo "pre_eol_info : $(cat "${dir}/device/pre_eol_info" \
            2>/dev/null || echo "n/a")"
        echo
    done

    if [[ "$found" -eq 0 ]]; then
        echo "No MMC/SD block devices; this player boots from something"
        echo "else (SSD, NVMe, USB)."
    fi

    return 0
}

# The richest evidence of a failing card by far, and host-only: the
# ring buffer is not readable from inside the server container, so
# this cannot move to the API. Matches the block layer's I/O error
# message, the mmc driver's timeouts and resets, and ext4's own
# remount-read-only announcement.
#
# Captured into a variable for the same reason as the under-voltage
# equivalent above: `grep | tail` exits 0 even when grep matched
# nothing, so an empty section would otherwise be ambiguous between
# "no errors" and "could not read dmesg".
storage_kernel_log() {
    local out
    out="$({ dmesg -T 2>/dev/null || sudo -n dmesg -T 2>/dev/null \
        || dmesg 2>/dev/null; } \
        | grep -i -E 'blk_update_request|I/O error|mmc[0-9]+:|mmcblk|EXT4-fs error|EXT4-fs .*(remount|read-only)|Buffer I/O error|critical (target|medium) error' \
        | tail -100)"

    if [[ -n "$out" ]]; then
        echo "$out"
    else
        echo "No storage errors in the kernel ring buffer."
        echo "(The buffer is finite, so this does not rule out errors"
        echo " earlier in this device's uptime.)"
    fi
}

# SMART, for the boards that boot from a SATA/NVMe device. Anthias
# reads this through the privileged viewer container and republishes
# it on /api/v2/info; this section is the host-side raw read for when
# the stack can't answer. smartmontools is not installed on the
# SD-card-only boards, and an SD card has no SMART to report anyway.
smart_devices() {
    local found=0 dev

    if ! command -v smartctl >/dev/null 2>&1; then
        echo "smartctl is not installed on this host."
        echo "(apt-get install smartmontools to read SMART here; the"
        echo " viewer container ships it on x86/arm64/pi5.)"
        return
    fi

    for dev in /dev/sd? /dev/nvme?n?; do
        [[ -b "$dev" ]] || continue
        found=1
        echo "=== ${dev}"
        sudo -n smartctl -H -A -i "$dev" 2>&1 | sed 's/^/    /'
        echo
    done

    if [[ "$found" -eq 0 ]]; then
        echo "No SATA/NVMe block devices on this player."
    fi

    return 0
}

note "storage health"
STORAGE="${REPORT_DIR}/storage.txt"
section "Block devices" "$STORAGE" lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT,RO
# `ro` on the root filesystem here is the endgame of a dying card: the
# player keeps displaying content while every change silently fails to
# save.
section "Mount flags (ro = filesystem has gone read-only)" "$STORAGE" \
    sh -c 'grep -E " (ext4|f2fs|btrfs|vfat) " /proc/self/mounts'
section "ext4 error counters (durable; see api-info.json)" "$STORAGE" \
    ext4_error_counters
section "MMC / SD devices" "$STORAGE" mmc_devices
section "SMART (SATA / NVMe; see api-info.json)" "$STORAGE" smart_devices
section "Storage kernel messages" "$STORAGE" storage_kernel_log

# Raspberry Pi specifics: model, firmware, power, temperature.
note "raspberry pi specifics (if present)"
PI="${REPORT_DIR}/raspberry-pi.txt"
section "Device model" "$PI" cat /proc/device-tree/model
section "Under-voltage sensor (raw; see api-info.json)" "$PI" \
    undervoltage_sensor
section "Under-voltage kernel messages" "$PI" undervoltage_kernel_log
# Kept for the live bits (0-3: under-voltage now, ARM capped, throttled,
# soft temp limit) and for thermal throttling, which hwmon does not
# expose. Bits 16-19 are the ones the hwmon driver keeps clearing.
section "Throttle flags (firmware; bits 16-19 unreliable)" "$PI" \
    vcgencmd get_throttled
section "Core temperature" "$PI" vcgencmd measure_temp
section "Firmware version" "$PI" vcgencmd version
section "Memory split" "$PI" vcgencmd get_mem arm
section "config.txt" "$PI" sh -c 'cat /boot/firmware/config.txt 2>/dev/null || cat /boot/config.txt 2>/dev/null'

# ---------------------------------------------------------------------------
# 2. Docker / compose state
# ---------------------------------------------------------------------------

note "docker engine & compose state"
DOCK="${REPORT_DIR}/docker.txt"
section "Docker version" "$DOCK" "${DOCKER[@]}" version
section "Docker info" "$DOCK" "${DOCKER[@]}" info
section "All containers" "$DOCK" "${DOCKER[@]}" ps -a
section "Images" "$DOCK" "${DOCKER[@]}" images
section "Volumes" "$DOCK" "${DOCKER[@]}" volume ls
section "Disk usage (docker)" "$DOCK" "${DOCKER[@]}" system df

if [[ -f "$COMPOSE_FILE" ]]; then
    section "Compose ps" "$DOCK" "${DOCKER[@]}" "${COMPOSE_ARGS[@]}" ps -a
    section "Compose config (resolved)" "$DOCK" "${DOCKER[@]}" "${COMPOSE_ARGS[@]}" config
else
    echo "No docker-compose.yml found at ${COMPOSE_FILE}" >>"$DOCK"
fi

# Per-container resource snapshot (one-shot, never blocks).
section "Container stats" "$DOCK" "${DOCKER[@]}" stats --no-stream

# Inspect each running Anthias container for restart counts / OOM / exit codes.
INSPECT="${REPORT_DIR}/container-inspect.txt"
note "container inspect (restarts, OOM, exit codes)"
for tag in "${CONTAINER_TAGS[@]}"; do
    cid="$("${DOCKER[@]}" ps -aq --filter "name=${tag}" 2>/dev/null | head -1)"
    [[ -z "$cid" ]] && continue
    section "Inspect ${tag} (state)" "$INSPECT" \
        "${DOCKER[@]}" inspect --format \
        'Status={{.State.Status}} Running={{.State.Running}} RestartCount={{.RestartCount}} OOMKilled={{.State.OOMKilled}} ExitCode={{.State.ExitCode}} Error={{.State.Error}} StartedAt={{.State.StartedAt}} FinishedAt={{.State.FinishedAt}}' \
        "$cid"
done

# ---------------------------------------------------------------------------
# 3. Container logs
# ---------------------------------------------------------------------------

note "container logs (${LOG_LINES} lines each)"

# Prefer compose logs (works regardless of logging driver); fall back to
# journald-by-tag (production driver) and finally `docker logs` by name.
if [[ -f "$COMPOSE_FILE" ]]; then
    "${DOCKER[@]}" "${COMPOSE_ARGS[@]}" logs --no-color --tail "$LOG_LINES" \
        >"${LOG_DIR}/compose-logs.txt" 2>&1 || true
fi

for tag in "${CONTAINER_TAGS[@]}"; do
    dest="${LOG_DIR}/${tag}.txt"

    # journald (production logging driver tags each service).
    if command -v journalctl >/dev/null 2>&1; then
        journalctl -t "$tag" -n "$LOG_LINES" --no-pager >"$dest" 2>/dev/null
        [[ -s "$dest" ]] && continue
    fi

    # Fall back to `docker logs` by container name.
    cid="$("${DOCKER[@]}" ps -aq --filter "name=${tag}" 2>/dev/null | head -1)"
    if [[ -n "$cid" ]]; then
        "${DOCKER[@]}" logs --tail "$LOG_LINES" "$cid" >"$dest" 2>&1 || true
    fi

    [[ -s "$dest" ]] || rm -f "$dest"
done

# Host-side Docker daemon log — explains failures to pull / start at all.
if command -v journalctl >/dev/null 2>&1; then
    journalctl -u docker --no-pager -n 500 \
        >"${LOG_DIR}/docker-daemon.txt" 2>/dev/null || true
fi

# Kernel ring buffer — OOM kills, USB/SD I/O errors, DRM/display faults.
note "kernel log (dmesg)"
if command -v dmesg >/dev/null 2>&1; then
    { dmesg -T 2>/dev/null || sudo dmesg -T 2>/dev/null || dmesg 2>/dev/null; } \
        >"${LOG_DIR}/dmesg.txt" 2>&1 || true
fi

# ---------------------------------------------------------------------------
# 4. Anthias config & database
# ---------------------------------------------------------------------------

note "anthias config (credentials redacted)"
CONF="${REPORT_DIR}/anthias-config.txt"
{
    echo "Config dir: ${CONFIG_DIR}"
    echo
    if [[ -f "${CONFIG_DIR}/anthias.conf" ]]; then
        echo "=== anthias.conf (passwords/keys redacted) ==="
        # Blank out anything that looks like a secret while keeping keys
        # visible so the operator can confirm a setting exists.
        sed -E 's/^([[:space:]]*(password|user|secret|token|key|cert|private)[^=]*=).*/\1 <redacted>/I' \
            "${CONFIG_DIR}/anthias.conf"
    else
        echo "No anthias.conf found at ${CONFIG_DIR}/anthias.conf"
    fi
} >"$CONF" 2>&1

section "Config dir listing" "$CONF" ls -la "$CONFIG_DIR"
section "Backups listing" "$CONF" ls -la "${CONFIG_DIR}/backups"

# Database overview — size, integrity, asset counts. Read-only queries
# against a copy so we never touch the live DB / WAL.
note "database overview"
DB="${REPORT_DIR}/database.txt"
DB_FILE="${CONFIG_DIR}/anthias.db"
[[ -f "$DB_FILE" ]] || DB_FILE="${CONFIG_DIR}/screenly.db"
if [[ -f "$DB_FILE" ]]; then
    section "Database file" "$DB" ls -la "$DB_FILE"
    # `is_reachable` is the single most useful column for triaging a
    # "web content doesn't display" report: the server marks a remote
    # asset unreachable when its reachability probe fails, and the
    # viewer then skips it. Keep it in the inventory on both paths.
    integrity_q='PRAGMA integrity_check;'
    journal_q='PRAGMA journal_mode;'
    tables_q='.tables'
    count_q='SELECT count(*) AS total, sum(is_enabled) AS enabled, sum(NOT is_reachable) AS unreachable FROM assets;'
    # Asset inventory without leaking full URLs of private content.
    inventory_q="SELECT substr(asset_id,1,8), substr(name,1,40), mimetype, is_enabled, is_reachable, substr(uri,1,60) FROM assets;"
    if command -v sqlite3 >/dev/null 2>&1; then
        section "Integrity check" "$DB" sqlite3 "file:${DB_FILE}?mode=ro" "$integrity_q"
        section "Journal mode" "$DB" sqlite3 "file:${DB_FILE}?mode=ro" "$journal_q"
        section "Tables" "$DB" sqlite3 "file:${DB_FILE}?mode=ro" "$tables_q"
        section "Asset count" "$DB" sqlite3 "file:${DB_FILE}?mode=ro" "$count_q"
        section "Assets (truncated)" "$DB" sqlite3 "file:${DB_FILE}?mode=ro" "$inventory_q"
    else
        echo "sqlite3 not installed on host — DB inspected via the server container" >>"$DB"
        # Fall back to the sqlite3 inside the server container. The host
        # config dir is bind-mounted at /data/.anthias, so the same DB
        # file is reachable there under its original basename.
        cid="$("${DOCKER[@]}" ps -q --filter "name=anthias-server" 2>/dev/null | head -1)"
        if [[ -n "$cid" ]]; then
            c_db="file:/data/.anthias/$(basename "$DB_FILE")?mode=ro"
            section "Integrity check (in container)" "$DB" \
                "${DOCKER[@]}" exec "$cid" sqlite3 "$c_db" "$integrity_q"
            section "Journal mode (in container)" "$DB" \
                "${DOCKER[@]}" exec "$cid" sqlite3 "$c_db" "$journal_q"
            section "Asset count (in container)" "$DB" \
                "${DOCKER[@]}" exec "$cid" sqlite3 "$c_db" "$count_q"
            section "Assets (truncated, in container)" "$DB" \
                "${DOCKER[@]}" exec "$cid" sqlite3 "$c_db" "$inventory_q"
        else
            echo "server container not running — cannot inspect DB" >>"$DB"
        fi
    fi
else
    echo "No database found under ${CONFIG_DIR}" >"$DB"
fi

# Assets directory — confirm media is actually on disk and how big it is.
section "Assets directory" "$DB" sh -c "ls -la '${USER_HOME}/anthias_assets' 2>/dev/null | head -50; echo; du -sh '${USER_HOME}/anthias_assets' 2>/dev/null"

# ---------------------------------------------------------------------------
# 4b. Video assets — ffprobe every video the player would try to play
# ---------------------------------------------------------------------------

# "Video won't play" is the most common bad-install symptom, so probe
# each video asset exactly the way processing.py does. The asset list
# comes straight from the live ORM (so local files resolve to their
# in-container /data/anthias_assets/<id> path and remote assets keep
# their http/rtsp URL), and ffprobe runs inside the server/celery image
# where it's guaranteed to exist — no host ffmpeg required.
note "video assets (ffprobe)"
VID="${REPORT_DIR}/video-assets.txt"
SERVER_CID="$("${DOCKER[@]}" ps -q --filter "name=anthias-server" 2>/dev/null | head -1)"
[[ -n "$SERVER_CID" ]] || \
    SERVER_CID="$("${DOCKER[@]}" ps -q --filter "name=anthias-celery" 2>/dev/null | head -1)"

{
    echo "ffprobe -v error -show_format -show_streams -print_format json <uri>"
    echo
    if [[ -z "$SERVER_CID" ]]; then
        echo "anthias-server/celery container not running — cannot enumerate or probe video assets."
    else
        # One uri per line from the ORM (mimetype == 'video'). Use a
        # bare django.setup() rather than `manage shell -c` — the latter
        # prints an "N objects imported automatically" banner that would
        # contaminate the uri list.
        mapfile -t VIDEO_URIS < <(
            "${DOCKER[@]}" exec "$SERVER_CID" python -c \
'import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "anthias_server.django_project.settings")
django.setup()
from anthias_server.app.models import Asset
for a in Asset.objects.filter(mimetype="video"):
    print(a.uri or "")' 2>/dev/null
        )

        if [[ ${#VIDEO_URIS[@]} -eq 0 ]]; then
            echo "No video assets found in the database."
        else
            echo "Probing ${#VIDEO_URIS[@]} video asset(s)."
            echo
        fi

        for uri in "${VIDEO_URIS[@]}"; do
            [[ -z "$uri" ]] && continue
            echo "================================================================"
            echo "URI: ${uri}"
            echo "----------------------------------------------------------------"
            # `timeout` caps a hung probe on an unreachable RTSP/stream URL.
            "${DOCKER[@]}" exec "$SERVER_CID" timeout 30 ffprobe \
                -v error -show_format -show_streams -print_format json "$uri" 2>&1 \
                || echo "(ffprobe failed, timed out, or file missing)"
            echo
        done
    fi
} >"$VID" 2>&1

# ---------------------------------------------------------------------------
# 5. Network & service reachability
# ---------------------------------------------------------------------------

note "network & reachability"
NET="${REPORT_DIR}/network.txt"
section "Interfaces" "$NET" sh -c 'ip addr 2>/dev/null || ifconfig 2>/dev/null'
section "Routes" "$NET" sh -c 'ip route 2>/dev/null || route -n 2>/dev/null'
section "DNS config" "$NET" cat /etc/resolv.conf
section "Listening sockets" "$NET" sh -c 'ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null'

# Local web UI reachability.
section "Local web UI (HTTP)" "$NET" sh -c \
    'curl -sS -o /dev/null -w "HTTP %{http_code} in %{time_total}s\n" --max-time 10 http://localhost/ || echo "unreachable"'
# Internet reachability (image registry / asset downloads).
section "Internet (ghcr.io)" "$NET" sh -c \
    'curl -sS -o /dev/null -w "HTTP %{http_code} in %{time_total}s\n" --max-time 10 https://ghcr.io/ || echo "unreachable"'
section "DNS resolution" "$NET" sh -c \
    'getent hosts ghcr.io || nslookup ghcr.io 2>/dev/null || echo "resolution failed"'

# Anthias's own view of the device, straight from /api/v2/info.
#
# This is the authoritative, already-interpreted device state: version,
# uptime, memory and the low-RAM gate, display power, IP addresses,
# power-supply health and storage health (including the write check,
# which nothing outside Anthias performs), produced by the same code
# the web UI renders
# from. Capturing it keeps the bundle in step with the API instead of
# re-deriving the same facts in shell, and it is where new diagnostics
# should land as more of them move into the UI: adding a field to the
# API puts it in the bundle for free.
#
# Best-effort by design. It needs the server up, and on a device with
# an auth backend configured it answers with a redirect to /login/
# rather than data. Both cases fall back to the host-only readings
# elsewhere in this bundle (kernel ring buffer, raw sysfs, container
# logs), which is the situation that prompts most debug bundles anyway.
#
# Port 80 is production; 8000 is the dev compose file.
#
# The timeout is deliberately generous. /api/v2/info calls
# get_node_ip(), which publishes a hostcmd and then waits up to ~80s
# (60s host_agent_ready + 20s ip_addresses_ready) for the host agent to
# populate Redis. A host agent that is not running is itself a common
# fault this bundle is meant to diagnose, so a 10s timeout would drop
# the API snapshot on precisely the broken devices that need it. The
# endpoint is not polled, so waiting is cheap and happens once.
API_FETCH_TIMEOUT_S=90

fetch_api_info() {
    local url code
    for url in "http://localhost/api/v2/info" \
               "http://localhost:8000/api/v2/info"; do
        code="$(curl -sS --max-time "$API_FETCH_TIMEOUT_S" -o "$API_INFO" \
            -w '%{http_code}' "$url" 2>/dev/null)" || continue
        # A 302 to /login/ still writes a body, so check the status and
        # that we actually got JSON rather than an HTML login page.
        if [[ "$code" == "200" ]] && head -c 1 "$API_INFO" | grep -q '{'; then
            note "fetched /api/v2/info from ${url%%/api*}"
            # Pretty-print when python3 is around; the raw single-line
            # body is still valid JSON if it isn't.
            if command -v python3 >/dev/null 2>&1; then
                python3 -m json.tool "$API_INFO" >"${API_INFO}.tmp" \
                    2>/dev/null && mv "${API_INFO}.tmp" "$API_INFO"
                rm -f "${API_INFO}.tmp"
            fi
            return 0
        fi
    done
    return 1
}

note "anthias api snapshot"
API_INFO="${REPORT_DIR}/api-info.json"
if ! fetch_api_info; then
    rm -f "$API_INFO"
    cat >"${REPORT_DIR}/api-info.txt" <<'EOF'
Could not read /api/v2/info.

The server did not answer on port 80 or 8000, or it redirected to the
login page because this device has an auth backend configured.

Device state that would normally come from here (power-supply health,
storage health, memory, uptime, version) has to be read from the raw
sections instead: raspberry-pi.txt, storage.txt, system.txt, logs/ and
redis.txt.
EOF
fi

# Redis health — broker / channel layer / viewer bus all live here.
note "redis health"
REDIS="${REPORT_DIR}/redis.txt"
cid="$("${DOCKER[@]}" ps -q --filter "name=anthias-redis" 2>/dev/null | head -1)"
if [[ -n "$cid" ]]; then
    section "Redis PING" "$REDIS" "${DOCKER[@]}" exec "$cid" redis-cli ping
    section "Redis INFO server" "$REDIS" "${DOCKER[@]}" exec "$cid" redis-cli info server
    section "Redis INFO memory" "$REDIS" "${DOCKER[@]}" exec "$cid" redis-cli info memory
    section "Redis INFO persistence" "$REDIS" "${DOCKER[@]}" exec "$cid" redis-cli info persistence
    section "Redis keyspace" "$REDIS" "${DOCKER[@]}" exec "$cid" redis-cli info keyspace
else
    echo "No anthias-redis container running" >"$REDIS"
fi

# ---------------------------------------------------------------------------
# 6. Git / version metadata
# ---------------------------------------------------------------------------

note "version metadata"
VER="${REPORT_DIR}/version.txt"
if [[ -d "${ANTHIAS_DIR}/.git" ]]; then
    section "Git describe" "$VER" git -C "$ANTHIAS_DIR" describe --tags --always
    section "Git log (last 10)" "$VER" git -C "$ANTHIAS_DIR" log --oneline -10
    section "Git status" "$VER" git -C "$ANTHIAS_DIR" status -sb
fi
section "Image tags in use" "$VER" sh -c \
    "grep -E 'image:|DOCKER_TAG|DEVICE_TYPE' '${COMPOSE_FILE}' 2>/dev/null; cat '${ANTHIAS_DIR}/.env' 2>/dev/null"

# ---------------------------------------------------------------------------
# 7. Wrap up
# ---------------------------------------------------------------------------

# Top-level summary so a reader knows the report's shape at a glance.
{
    echo "Anthias debug report"
    echo "Generated : $(date 2>/dev/null)"
    echo "User      : ${RUN_USER}"
    echo "Host      : $(hostname 2>/dev/null)"
    echo "Anthias   : ${ANTHIAS_DIR}"
    echo "Config    : ${CONFIG_DIR}"
    echo
    echo "Files in this report:"
    ( cd "$REPORT_DIR" && find . -type f | sort )
} >"${REPORT_DIR}/README.txt" 2>&1

# Redact PII across the whole bundle as the very last step before it
# leaves this machine.
note "redacting PII (IP/MAC/email/credentials/hostname)"
scrub_pii

# Fix ownership when invoked under sudo so the operator can read/delete it.
if [[ -n "${SUDO_USER:-}" ]]; then
    chown -R "${SUDO_USER}:${SUDO_USER}" "$REPORT_DIR" 2>/dev/null || true
fi

echo
if [[ "$MAKE_ARCHIVE" -eq 1 ]]; then
    ARCHIVE="${OUTPUT_BASE}/${REPORT_NAME}.tar.gz"
    if tar -czf "$ARCHIVE" -C "$OUTPUT_BASE" "$REPORT_NAME" 2>/dev/null; then
        rm -rf "$REPORT_DIR"
        [[ -n "${SUDO_USER:-}" ]] && chown "${SUDO_USER}:${SUDO_USER}" "$ARCHIVE" 2>/dev/null || true
        echo "Debug bundle written to:"
        echo "  ${ARCHIVE}"
        echo
        echo "Attach this file to your GitHub issue or forum post."
        echo "PII (IP/MAC/email/credentials/hostname) and anthias.conf"
        echo "secrets were redacted; skim the bundle before sharing."
    else
        echo "Archiving failed; the uncompressed report is at:"
        echo "  ${REPORT_DIR}"
    fi
else
    echo "Debug report written to:"
    echo "  ${REPORT_DIR}"
fi
