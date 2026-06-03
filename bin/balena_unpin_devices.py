#!/usr/bin/env python3
"""Bring pinned/stale devices current across the Anthias balena fleets.

Devices flashed from pre-#2098 disk images provision themselves pinned
to the release that was baked into the image, so they sit on that old
release forever instead of tracking the fleet's OTA channel — and new
such devices keep coming online as people flash old images. This runs
hourly from .github/workflows/balena-unpin-devices.yaml.

It has three independent phases, each dry-run by default and each
selectable on its own:

  1. unpin (always on) — clear the app-release pin so the device tracks
     the fleet's latest stable app. ONE filtered bulk PATCH per fleet.
  2. --os-update — start a host OS update (resinhup) toward the latest
     balenaOS for the device type, so unpinned-but-old-OS devices also
     move forward. Per-device cloud action, bounded to a tranche per run.
  3. --supervisor — bump the supervisor to the newest release for the
     device's CPU architecture. Restricted to devices already on the
     target balenaOS so the supervisor/OS pairing stays compatible
     (devices still mid-OS-update get their matching supervisor from the
     HUP itself), and skipped entirely for device types frozen on a
     legacy balenaOS line (e.g. pi2 tops out at 5.1.x and runs
     supervisor 15.x — the newest supervisor isn't built for it).

bin/balena_fleet_maintenance.py (the CLI-driven staged OS roller) does
the same OS-update + unpin but cannot target the pinned population:
`balena device list` does not expose the per-device pin, so it selects
by OS version and leaves on-target-but-pinned devices out of scope. This
script instead talks to the balena cloud API directly — the pin, the
host-OS releases, the supervisor releases and the resinhup action are
all reachable there with nothing but a token, so the one hourly job
brings the whole fleet current without a CLI install.

The pin lives in the device's `is_pinned_on__release` field. Do NOT
use the similarly-named `should_be_running__release`: on devices it
is computed (the pin when set, otherwise the fleet's tracked
release), so filtering it for `ne null` matches every device in the
fleet, and the API rejects it as a PATCH body property. The supervisor,
by contrast, IS set through `should_be_managed_by__release`.

Notes:

  * dry-run by default — nothing mutates without --apply;
  * the unpin is ONE filtered bulk PATCH per fleet (PineJS applies the
    body to every device matching the $filter), so a 15k-device
    backlog clears in a single request instead of hours of per-device
    calls;
  * the OS update is per-device (a resinhup is not a bulk PATCH) and so
    is bounded to --os-percent of each online fleet per run (default 5%,
    or an absolute --os-limit), the same staged-ramp idea as
    balena_fleet_maintenance.py — the hourly cadence walks the backlog
    forward a slice at a time instead of stampeding every device into a
    simultaneous download+reboot;
  * the OS update is online-only (resinhup needs the device reachable)
    and skips devices whose current OS is too old for a single-hop HUP
    (balenaOS < 2.14.0); the unpin, being cloud-side state, still
    includes offline devices;
  * devices carrying the `anthias_keep_pinned` tag (any value) are
    excluded from every phase — deliberately pinned canaries/testbeds
    keep their OS and supervisor too;
  * a failure on one fleet (or one device's HUP) is logged and counted,
    never aborting the rest;
  * output is aggregate-only (per-fleet counts and a pinned-release
    histogram) because the hourly workflow's logs are world-readable —
    this is a public repo. Per-device uuid lines need --verbose, which
    must stay out of CI.

Requires a balena API token in $BALENA_TOKEN (the same secret the
deploy workflows use). Examples:

    BALENA_TOKEN=... bin/balena_unpin_devices.py             # dry-run
    BALENA_TOKEN=... bin/balena_unpin_devices.py --apply
    # unpin + roll a 5% OS-update tranche + bump supervisors:
    BALENA_TOKEN=... bin/balena_unpin_devices.py \
        --os-update --supervisor --apply
    BALENA_TOKEN=... bin/balena_unpin_devices.py \
        --fleet screenly_ose/anthias-pi4 --os-update --os-limit 50 --apply
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any

API_ROOT = 'https://api.balena-cloud.com'
API_BASE = f'{API_ROOT}/v7'

# Keep in sync with FLEET_DEVICE_TYPE in bin/balena_fleet_maintenance.py.
FLEETS = [
    'screenly_ose/anthias-pi2',
    'screenly_ose/anthias-pi3',
    'screenly_ose/anthias-pi4',
    'screenly_ose/anthias-pi5',
    'screenly_ose/anthias-x86',
]

# fleet slug -> balenaOS device-type slug (for OS-release + CPU-arch
# lookups). Matches FLEET_DEVICE_TYPE in bin/balena_fleet_maintenance.py
# and the matrix in .github/workflows/build-balena-disk-image.yaml.
FLEET_DEVICE_TYPE = {
    'screenly_ose/anthias-pi2': 'raspberry-pi2',
    'screenly_ose/anthias-pi3': 'raspberrypi3',
    'screenly_ose/anthias-pi4': 'raspberrypi4-64',
    'screenly_ose/anthias-pi5': 'raspberrypi5',
    'screenly_ose/anthias-x86': 'generic-amd64',
}

KEEP_PINNED_TAG = 'anthias_keep_pinned'

# The device-actions host (actions.balena-devices.com) sits behind
# Cloudflare, which 403s the default `Python-urllib/x.y` User-Agent as a
# banned client signature (error 1010). A descriptive UA passes, so set
# one on every request (api.balena-cloud.com doesn't need it, but it's
# harmless there and keeps the calls identifiable in balena's logs).
USER_AGENT = 'anthias-fleet-maintenance/1.0'

# A balenaOS HUP (resinhup) is only allowed from >= 2.14.0 to >= 2.16.0
# (balena-hup-action-utils `actionsConfig`). None of the Anthias device
# types are jetson-* boards, so the takeover/major-version special cases
# don't apply — a device on >= 2.14.0 can go straight to the latest.
HUP_MIN_SOURCE = (2, 14, 0, 0)
HUP_MIN_TARGET = (2, 16, 0, 0)

# `getSupervisorReleasesForCpuArchitecture` returns the newest supervisor
# for an architecture regardless of OS — but the newest supervisor is
# built for the current calendar-versioned balenaOS line and is NOT safe
# on a device type frozen on a legacy OS (e.g. raspberry-pi2 tops out at
# balenaOS 5.1.x, whose devices run supervisor 15.x; pushing 17.x there
# would likely break them). So only bump the supervisor on fleets whose
# target OS is on the calendar-versioned line (major >= 2025). balenaOS
# switched from semver (2.x .. 7.x) to YYYY.M.P calendar versioning, so a
# major in the thousands cleanly marks "still getting current OS+
# supervisor builds"; legacy-OS fleets keep their OS-matched supervisor.
SUPERVISOR_MIN_TARGET_OS_MAJOR = 2025

# Defensive pagination: the API returns the full set by default, but a
# bounded page keeps one pathological fleet listing from becoming one
# huge response body.
PAGE_SIZE = 500

# Per-device log lines printed per fleet under --verbose before
# collapsing into the release histogram.
DETAIL_CAP = 25


def api_request(
    token: str,
    method: str,
    resource: str,
    params: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 60,
    base: str = API_BASE,
) -> Any:
    """One balena API call. Returns parsed JSON (None for an empty or
    non-JSON body); raises HTTPError/URLError/OSError on failure."""
    url = f'{base}/{resource}'
    if params:
        # Pine rejects percent-encoded OData syntax ("Malformed url"),
        # so keep its structural characters literal and only encode the
        # rest (spaces, the quoted values' contents, ...).
        url += '?' + '&'.join(
            f'{key}={urllib.parse.quote(value, safe="$=(),:/'")}'
            for key, value in params.items()
        )
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'User-Agent': USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    try:
        return json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError:
        # Filtered PATCH returns a plain-text "OK" body.
        return None


# --------------------------------------------------------------------------
# Version helpers (mirror bin/balena_fleet_maintenance.py).
# --------------------------------------------------------------------------


def parse_version(raw: str) -> tuple[int, int, int, int]:
    """Normalize 'balenaOS 6.1.24+rev4' / 'v6.12.3+rev4' / '16.5.0' to a
    comparable (major, minor, patch, rev) tuple. Unparseable -> all zeros
    (treated as oldest)."""
    s = raw.replace('balenaOS', '').strip().lstrip('v')
    rev = 0
    if '+rev' in s:
        s, _, rev_s = s.partition('+rev')
        rev_digits = ''.join(c for c in rev_s if c.isdigit())
        rev = int(rev_digits) if rev_digits else 0
    # Drop a +variant / -prerelease suffix before splitting on '.'.
    s = s.split('+')[0].split('-')[0]
    nums = []
    for part in s.split('.')[:3]:
        digits = ''.join(c for c in part if c.isdigit())
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2], rev)


def normalize_os(raw: str) -> str:
    """'balenaOS 6.1.24+rev4' -> '6.1.24+rev4' (the form resinhup wants)."""
    return raw.replace('balenaOS', '').strip().lstrip('v')


# --------------------------------------------------------------------------
# Pin (app release) phase — unchanged behaviour.
# --------------------------------------------------------------------------


def pinned_filter(fleet: str, exclude_kept: bool) -> str:
    flt = (
        f"belongs_to__application/any(a:a/slug eq '{fleet}')"
        ' and is_pinned_on__release ne null'
    )
    if exclude_kept:
        flt += f" and not(device_tag/any(t:t/tag_key eq '{KEEP_PINNED_TAG}'))"
    return flt


def list_pinned_devices(token: str, fleet: str) -> list[dict[str, Any]]:
    """All devices in `fleet` whose release pin is set (including ones
    tagged keep-pinned, so the report can show them)."""
    devices: list[dict[str, Any]] = []
    skip = 0
    while True:
        page = api_request(
            token,
            'GET',
            'device',
            params={
                '$filter': pinned_filter(fleet, exclude_kept=False),
                '$select': 'id,uuid,is_online',
                '$expand': (
                    'device_tag($select=tag_key),'
                    'is_pinned_on__release($select=raw_version)'
                ),
                '$orderby': 'id asc',
                '$top': str(PAGE_SIZE),
                '$skip': str(skip),
            },
        )
        batch = page.get('d', []) if isinstance(page, dict) else []
        devices.extend(batch)
        if len(batch) < PAGE_SIZE:
            return devices
        skip += PAGE_SIZE


def count_pinned(token: str, fleet: str, exclude_kept: bool) -> int:
    result = api_request(
        token,
        'GET',
        'device/$count',
        params={'$filter': pinned_filter(fleet, exclude_kept)},
    )
    return int(result['d']) if isinstance(result, dict) else -1


def unpin_fleet(token: str, fleet: str) -> None:
    """Clear the release pin on every pinned, not-keep-pinned device in
    `fleet` with one filtered bulk PATCH."""
    api_request(
        token,
        'PATCH',
        'device',
        params={'$filter': pinned_filter(fleet, exclude_kept=True)},
        body={'is_pinned_on__release': None},
        # One PATCH may rewrite >10k rows; give the API room.
        timeout=300,
    )


def is_keep_pinned(device: dict[str, Any]) -> bool:
    return any(
        tag.get('tag_key') == KEEP_PINNED_TAG
        for tag in device.get('device_tag') or []
    )


def pinned_version(device: dict[str, Any]) -> str:
    """Human-readable version of the release the device is pinned to."""
    expanded = device.get('is_pinned_on__release') or []
    if isinstance(expanded, list) and expanded:
        return str(expanded[0].get('raw_version') or '?')
    return '?'


def report_pinned(
    devices: list[dict[str, Any]], apply: bool, verbose: bool
) -> None:
    """Pinned-release histogram; per-device lines only under --verbose.

    The default output deliberately carries no device identifiers —
    the hourly workflow's logs are world-readable on this public repo,
    so uuids (correlatable customer device data) must never reach
    stdout there.
    """
    if verbose:
        for dev in devices[:DETAIL_CAP]:
            uuid = str(dev.get('uuid', ''))[:12]
            online = 'online' if dev.get('is_online') else 'offline'
            release = pinned_version(dev)
            if is_keep_pinned(dev):
                print(
                    f'  [keep] {uuid} {online} pinned to {release} '
                    f'({KEEP_PINNED_TAG} tag)'
                )
            else:
                verb = 'unpin' if apply else 'plan'
                print(f'  [{verb}] {uuid} {online} pinned to {release}')
        if len(devices) > DETAIL_CAP:
            print(f'  ... and {len(devices) - DETAIL_CAP} more')
    if devices:
        print('  by pinned release:')
        histogram = Counter(pinned_version(dev) for dev in devices)
        for release, count in histogram.most_common():
            print(f'    {count:6d} x {release}')


# --------------------------------------------------------------------------
# OS-update (resinhup) phase.
# --------------------------------------------------------------------------


def actions_base(token: str) -> str | None:
    """The host for device actions (resinhup) — `https://actions.<base>`,
    where <base> is the cloud's configured deviceUrlsBase. Resolved at
    runtime from /config so nothing is hardcoded. None on failure."""
    config = api_request(token, 'GET', 'config', base=API_ROOT)
    device_urls_base = (
        config.get('deviceUrlsBase') if isinstance(config, dict) else None
    )
    return f'https://actions.{device_urls_base}' if device_urls_base else None


def latest_os_version(token: str, device_type: str) -> str | None:
    """Newest final balenaOS release for a device type, e.g. '2026.1.0'.

    Mirrors `balena os versions <type>` via the cloud API: the host
    application owns the OS releases; take the newest successful, final,
    non-invalidated one. None if the lookup fails or finds nothing."""
    flt = (
        "status eq 'success' and is_final eq true and is_invalidated eq false"
        ' and belongs_to__application/any(a:a/is_host eq true'
        f" and a/is_for__device_type/any(dt:dt/slug eq '{device_type}'))"
    )
    result = api_request(
        token,
        'GET',
        'release',
        params={
            '$top': '1',
            '$select': 'raw_version',
            '$orderby': (
                'semver_major desc,semver_minor desc,'
                'semver_patch desc,revision desc'
            ),
            '$filter': flt,
        },
    )
    rows = result.get('d', []) if isinstance(result, dict) else []
    return rows[0].get('raw_version') if rows else None


def list_fleet_devices(token: str, fleet: str) -> list[dict[str, Any]]:
    """All ONLINE devices in `fleet` with the fields the OS-update and
    supervisor phases need (id, uuid, OS + supervisor version, keep-pinned
    tag). Offline devices can't run a resinhup, so they're excluded here
    (the unpin phase, which is cloud-side, still covers them)."""
    devices: list[dict[str, Any]] = []
    skip = 0
    flt = (
        f"belongs_to__application/any(a:a/slug eq '{fleet}')"
        ' and is_online eq true'
    )
    while True:
        page = api_request(
            token,
            'GET',
            'device',
            params={
                '$filter': flt,
                '$select': 'id,uuid,os_version,supervisor_version',
                '$expand': 'device_tag($select=tag_key)',
                '$orderby': 'uuid asc',
                '$top': str(PAGE_SIZE),
                '$skip': str(skip),
            },
        )
        batch = page.get('d', []) if isinstance(page, dict) else []
        devices.extend(batch)
        if len(batch) < PAGE_SIZE:
            return devices
        skip += PAGE_SIZE


def hup_eligible(device: dict[str, Any], target_os: str) -> bool:
    """A device can be HUP'd to target_os when it isn't already there,
    isn't keep-pinned, and its current OS is recent enough for a
    single-hop balenaOS update (>= 2.14.0)."""
    current = normalize_os(device.get('os_version') or '')
    if not current or is_keep_pinned(device):
        return False
    cur = parse_version(current)
    return (
        cur != parse_version(target_os)
        and cur >= HUP_MIN_SOURCE
        and parse_version(target_os) >= HUP_MIN_TARGET
        and cur < parse_version(target_os)
    )


def select_os_tranche(
    eligible: list[dict[str, Any]], percent: float, limit: int
) -> list[dict[str, Any]]:
    """Bound the OS-update set for one run: an absolute --os-limit if
    given (>0), else ceil(percent% of the eligible population). Devices
    are already uuid-ordered, so the slice is a stable representative
    sample that walks forward run over run as earlier devices land."""
    if not eligible:
        return []
    if limit > 0:
        return eligible[:limit]
    size = max(1, math.ceil(len(eligible) * percent / 100.0))
    return eligible[:size]


def start_os_update(
    token: str, base: str, uuid: str, target_os: str, timeout: float = 60
) -> None:
    """Trigger a detached resinhup on one device. POSTs the same action
    the balena SDK's startOsUpdate does:
    `POST https://actions.<base>/v2/<uuid>/resinhup`. Raises on failure."""
    req = urllib.request.Request(
        f'{base}/v2/{uuid}/resinhup',
        data=json.dumps(
            {'parameters': {'target_version': target_os}}
        ).encode(),
        method='POST',
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'User-Agent': USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout):
        pass


# --------------------------------------------------------------------------
# Supervisor phase.
# --------------------------------------------------------------------------


def cpu_architecture(token: str, device_type: str) -> str | None:
    """CPU-architecture slug (e.g. 'aarch64') for a device-type slug."""
    result = api_request(
        token,
        'GET',
        'device_type',
        params={
            '$select': 'slug',
            '$expand': 'is_of__cpu_architecture($select=slug)',
            '$filter': f"slug eq '{device_type}'",
        },
    )
    rows = result.get('d', []) if isinstance(result, dict) else []
    if not rows:
        return None
    arch = rows[0].get('is_of__cpu_architecture') or []
    return arch[0].get('slug') if arch else None


def latest_supervisor_release(
    token: str, cpu_arch: str
) -> tuple[int, str] | None:
    """(release id, raw_version) of the newest supervisor release for a
    CPU architecture, mirroring the SDK's
    getSupervisorReleasesForCpuArchitecture. None if none found."""
    flt = (
        "status eq 'success' and is_final eq true and is_invalidated eq false"
        ' and semver_major gt 0 and belongs_to__application/any(a:'
        "startswith(a/slug,'balena_os/') and endswith(a/slug,'-supervisor')"
        ' and a/is_host eq false and a/is_for__device_type/any(dt:'
        f"dt/is_of__cpu_architecture/any(c:c/slug eq '{cpu_arch}')))"
    )
    result = api_request(
        token,
        'GET',
        'release',
        params={
            '$top': '1',
            '$select': 'id,raw_version',
            '$orderby': (
                'semver_major desc,semver_minor desc,'
                'semver_patch desc,revision desc'
            ),
            '$filter': flt,
        },
    )
    rows = result.get('d', []) if isinstance(result, dict) else []
    if not rows:
        return None
    return int(rows[0]['id']), str(rows[0].get('raw_version') or '?')


def supervisor_eligible(
    device: dict[str, Any], target_os: str, latest_sup: str
) -> bool:
    """Bump the supervisor only on devices that are already on the target
    balenaOS (so the supervisor/OS pairing stays within balena's
    compatibility window — devices still mid-HUP get their matching
    supervisor from the OS update itself) and are behind the newest
    supervisor for their architecture, and aren't keep-pinned."""
    if is_keep_pinned(device):
        return False
    current_os = normalize_os(device.get('os_version') or '')
    if not current_os or parse_version(current_os) != parse_version(target_os):
        return False
    current_sup = device.get('supervisor_version') or ''
    if not current_sup:
        return False
    return parse_version(current_sup) < parse_version(latest_sup)


def pin_supervisor(token: str, ids: list[int], release_id: int) -> None:
    """Point a set of devices at a supervisor release with one filtered
    bulk PATCH of `should_be_managed_by__release`."""
    id_list = ','.join(str(i) for i in ids)
    api_request(
        token,
        'PATCH',
        'device',
        params={'$filter': f'id in ({id_list})'},
        body={'should_be_managed_by__release': release_id},
        timeout=300,
    )


# --------------------------------------------------------------------------
# Driver.
# --------------------------------------------------------------------------


def run_unpin_phase(
    token: str, fleet: str, apply: bool, verbose: bool, totals: dict[str, int]
) -> None:
    devices = list_pinned_devices(token, fleet)
    kept = sum(1 for dev in devices if is_keep_pinned(dev))
    totals['pinned'] += len(devices)
    totals['kept'] += kept
    print(f'\n# {fleet}: pinned={len(devices)} keep-tagged={kept}')
    report_pinned(devices, apply, verbose)
    if not apply or len(devices) == kept:
        return
    try:
        unpin_fleet(token, fleet)
        remaining = count_pinned(token, fleet, exclude_kept=True)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors='replace')[:200]
        print(
            f'  ! bulk unpin failed: HTTP {exc.code}: {detail}',
            file=sys.stderr,
        )
        totals['failed'] += 1
        return
    except OSError as exc:
        print(f'  ! bulk unpin failed: {exc}', file=sys.stderr)
        totals['failed'] += 1
        return
    totals['unpinned'] += len(devices) - kept - max(remaining, 0)
    if remaining:
        # -1 = the post-PATCH recount itself failed.
        print(
            f'  ! {remaining} device(s) still pinned after the bulk PATCH',
            file=sys.stderr,
        )
        totals['failed'] += 1
    else:
        print(f'  unpinned {len(devices) - kept} device(s)')


def run_os_update_phase(
    token: str,
    fleet: str,
    devices: list[dict[str, Any]],
    target_os: str,
    base: str,
    percent: float,
    limit: int,
    apply: bool,
    verbose: bool,
    totals: dict[str, int],
) -> None:
    eligible = [d for d in devices if hup_eligible(d, target_os)]
    tranche = select_os_tranche(eligible, percent, limit)
    totals['os_eligible'] += len(eligible)
    print(
        f'  os-update -> {target_os}: online={len(devices)} '
        f'eligible={len(eligible)} tranche={len(tranche)}'
    )
    for dev in tranche:
        cur = normalize_os(dev.get('os_version') or '') or '?'
        if verbose:
            verb = 'os' if apply else 'plan-os'
            print(
                f'  [{verb}] {str(dev.get("uuid", ""))[:12]} {cur} -> {target_os}'
            )
        if not apply:
            continue
        try:
            start_os_update(token, base, dev['uuid'], target_os)
            totals['os_started'] += 1
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors='replace')[:120]
            if verbose:
                print(
                    f'  ! os-update failed for {str(dev.get("uuid", ""))[:12]}:'
                    f' HTTP {exc.code}: {detail}',
                    file=sys.stderr,
                )
            totals['os_failed'] += 1
        except OSError:
            totals['os_failed'] += 1
    if apply and not verbose and totals['os_started']:
        print(f'  started {len(tranche)} OS update(s) (this fleet+run)')


def run_supervisor_phase(
    token: str,
    fleet: str,
    device_type: str,
    devices: list[dict[str, Any]],
    target_os: str,
    percent: float,
    limit: int,
    apply: bool,
    totals: dict[str, int],
) -> None:
    if parse_version(target_os)[0] < SUPERVISOR_MIN_TARGET_OS_MAJOR:
        # Frozen legacy-OS device type (e.g. pi2 on balenaOS 5.1.x): the
        # newest supervisor isn't built for it, so leave its OS-matched
        # supervisor alone.
        print(
            f'  supervisor: skipped — {fleet} is on the legacy balenaOS '
            f'{target_os} line; latest supervisor targets calendar-'
            f'versioned OS'
        )
        return
    arch = cpu_architecture(token, device_type)
    if not arch:
        print(
            f'  ! supervisor: cannot resolve CPU arch for {device_type}',
            file=sys.stderr,
        )
        totals['failed'] += 1
        return
    latest = latest_supervisor_release(token, arch)
    if not latest:
        print(f'  ! supervisor: no release found for {arch}', file=sys.stderr)
        totals['failed'] += 1
        return
    release_id, latest_ver = latest
    eligible = [
        d for d in devices if supervisor_eligible(d, target_os, latest_ver)
    ]
    # Same staged-ramp bound as the OS phase: pointing thousands of
    # devices at a new supervisor at once would have them all pull it on
    # their next poll, so walk the backlog forward a slice per run.
    tranche = select_os_tranche(eligible, percent, limit)
    totals['sup_eligible'] += len(eligible)
    print(
        f'  supervisor -> {latest_ver} ({arch}): '
        f'behind-and-on-target={len(eligible)} tranche={len(tranche)}'
    )
    if not apply or not tranche:
        return
    try:
        pin_supervisor(token, [d['id'] for d in tranche], release_id)
        totals['sup_updated'] += len(tranche)
        print(f'  pointed {len(tranche)} device(s) at supervisor {latest_ver}')
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors='replace')[:200]
        print(
            f'  ! supervisor PATCH failed: HTTP {exc.code}: {detail}',
            file=sys.stderr,
        )
        totals['failed'] += 1
    except OSError as exc:
        print(f'  ! supervisor PATCH failed: {exc}', file=sys.stderr)
        totals['failed'] += 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description='Bring pinned/stale devices current across the Anthias '
        'balena fleets (unpin app + optional OS-update + supervisor).'
    )
    ap.add_argument(
        '--fleet',
        action='append',
        dest='fleets',
        metavar='SLUG',
        help='fleet slug, e.g. screenly_ose/anthias-pi4 (repeatable; '
        'default: all five anthias fleets)',
    )
    ap.add_argument(
        '--os-update',
        action='store_true',
        help='also start a host OS update toward the latest balenaOS on a '
        'bounded tranche of each fleet (online devices only)',
    )
    ap.add_argument(
        '--os-percent',
        type=float,
        default=5.0,
        help='staged-ramp tranche size as %% of the eligible population '
        'per run, applied to both the OS-update and supervisor phases '
        '(default 5)',
    )
    ap.add_argument(
        '--os-limit',
        type=int,
        default=0,
        help='absolute tranche size per fleet for the OS-update and '
        'supervisor phases; overrides --os-percent when > 0',
    )
    ap.add_argument(
        '--supervisor',
        action='store_true',
        help='also bump the supervisor to the newest release for the arch, '
        'on devices already on the target balenaOS',
    )
    ap.add_argument(
        '--apply',
        action='store_true',
        help='actually perform changes (default: dry-run)',
    )
    ap.add_argument(
        '--verbose',
        action='store_true',
        help='print per-device uuid lines — local use only, NEVER in '
        'CI (the public repo publishes the workflow logs)',
    )
    args = ap.parse_args()

    token = os.environ.get('BALENA_TOKEN', '').strip()
    if not token:
        print('BALENA_TOKEN is not set', file=sys.stderr)
        return 2

    fleets = args.fleets or FLEETS
    mode = 'APPLY' if args.apply else 'DRY-RUN'
    phases = ['unpin']
    if args.os_update:
        phases.append('os-update')
    if args.supervisor:
        phases.append('supervisor')
    print(f'== balena fleet refresh ({mode}) — phases: {", ".join(phases)} ==')

    base = None
    if args.os_update:
        base = actions_base(token)
        if not base:
            print(
                'Could not resolve the device-actions host; skipping '
                'OS-update phase.',
                file=sys.stderr,
            )

    totals = {
        'pinned': 0,
        'kept': 0,
        'unpinned': 0,
        'os_eligible': 0,
        'os_started': 0,
        'os_failed': 0,
        'sup_eligible': 0,
        'sup_updated': 0,
        'failed': 0,
    }
    for fleet in fleets:
        try:
            run_unpin_phase(token, fleet, args.apply, args.verbose, totals)
        except OSError as exc:
            print(
                f'\n# {fleet}: device listing failed: {exc}', file=sys.stderr
            )
            totals['failed'] += 1
            continue

        if not (args.os_update or args.supervisor):
            continue
        device_type = FLEET_DEVICE_TYPE.get(fleet)
        if device_type is None:
            print(f'  ! unknown device type for {fleet}', file=sys.stderr)
            totals['failed'] += 1
            continue
        target_os = latest_os_version(token, device_type)
        if not target_os:
            print(
                f'  ! cannot resolve target OS for {device_type}',
                file=sys.stderr,
            )
            totals['failed'] += 1
            continue
        try:
            devices = list_fleet_devices(token, fleet)
        except OSError as exc:
            print(f'  ! online device listing failed: {exc}', file=sys.stderr)
            totals['failed'] += 1
            continue

        if args.os_update and base:
            run_os_update_phase(
                token,
                fleet,
                devices,
                normalize_os(target_os),
                base,
                args.os_percent,
                args.os_limit,
                args.apply,
                args.verbose,
                totals,
            )
        if args.supervisor:
            run_supervisor_phase(
                token,
                fleet,
                device_type,
                devices,
                normalize_os(target_os),
                args.os_percent,
                args.os_limit,
                args.apply,
                totals,
            )

    print(
        f'\n== summary: pinned={totals["pinned"]} kept={totals["kept"]} '
        f'unpinned={totals["unpinned"]} | os: eligible={totals["os_eligible"]} '
        f'started={totals["os_started"]} failed={totals["os_failed"]} | '
        f'supervisor: eligible={totals["sup_eligible"]} '
        f'updated={totals["sup_updated"]} | errors={totals["failed"]} =='
    )
    if not args.apply:
        print('Dry-run only. Re-run with --apply to make changes.')
    return 1 if totals['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
