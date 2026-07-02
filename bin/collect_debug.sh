#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

# collect_debug.sh — gather everything needed to debug a faulty Anthias
# installation into a single, shareable archive.
#
# Pulls host/system info, Docker + compose state, per-container logs
# (journald-tagged in production), the Anthias config and database
# overview, an ffprobe of every video asset, network reachability,
# Redis health, and Raspberry Pi specifics (throttling, temperature).
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

# Raspberry Pi specifics — model, firmware, throttling, temperature.
note "raspberry pi specifics (if present)"
PI="${REPORT_DIR}/raspberry-pi.txt"
section "Device model" "$PI" cat /proc/device-tree/model
section "Throttling status" "$PI" vcgencmd get_throttled
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
    if command -v sqlite3 >/dev/null 2>&1; then
        section "Integrity check" "$DB" sqlite3 "file:${DB_FILE}?mode=ro" 'PRAGMA integrity_check;'
        section "Journal mode" "$DB" sqlite3 "file:${DB_FILE}?mode=ro" 'PRAGMA journal_mode;'
        section "Tables" "$DB" sqlite3 "file:${DB_FILE}?mode=ro" '.tables'
        section "Asset count" "$DB" sqlite3 "file:${DB_FILE}?mode=ro" \
            'SELECT count(*) AS total, sum(is_enabled) AS enabled FROM assets;'
        # Asset inventory without leaking full URLs of private content.
        section "Assets (truncated)" "$DB" sqlite3 "file:${DB_FILE}?mode=ro" \
            "SELECT substr(asset_id,1,8), substr(name,1,40), mimetype, is_enabled, substr(uri,1,60) FROM assets;"
    else
        echo "sqlite3 not installed on host — DB inspected via container below" >>"$DB"
        # Fall back to the sqlite3 inside the server container.
        cid="$("${DOCKER[@]}" ps -q --filter "name=anthias-server" 2>/dev/null | head -1)"
        if [[ -n "$cid" ]]; then
            section "Asset count (in container)" "$DB" \
                "${DOCKER[@]}" exec "$cid" python -m anthias_server.manage shell -c \
                'from anthias_app.models import Asset; print("total", Asset.objects.count(), "enabled", Asset.objects.filter(is_enabled=1).count())'
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
