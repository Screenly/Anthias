#!/usr/bin/env python3
"""Unpin every pinned device across the Anthias balena fleets.

Devices flashed from pre-#2098 disk images provision themselves pinned
to the release that was baked into the image, so they sit on that old
release forever instead of tracking the fleet's OTA channel — and new
such devices keep coming online as people flash old images. This runs
hourly from .github/workflows/balena-unpin-devices.yaml and clears the
pin (the cloud-side equivalent of `balena device track-fleet`) on
every pinned device.

bin/balena_fleet_maintenance.py (the staged OS roller) cannot target
these: `balena device list` does not expose the per-device pin, so it
selects by OS version and explicitly leaves on-target-but-pinned
devices out of scope. This script instead queries the balena cloud API
directly, where the pin is filterable — the selection is exactly the
pinned population, nothing else.

The pin lives in the device's `is_pinned_on__release` field. Do NOT
use the similarly-named `should_be_running__release`: on devices it
is computed (the pin when set, otherwise the fleet's tracked
release), so filtering it for `ne null` matches every device in the
fleet, and the API rejects it as a PATCH body property.

Notes:

  * dry-run by default — nothing mutates without --apply;
  * the unpin is ONE filtered bulk PATCH per fleet (PineJS applies the
    body to every device matching the $filter), so a 15k-device
    backlog clears in a single request instead of hours of per-device
    calls;
  * offline devices are included — the pin is cloud-side state and the
    cleared value applies when the device next connects;
  * devices carrying the `anthias_keep_pinned` tag (any value) are
    excluded in the PATCH filter itself, so deliberately pinned
    canaries/testbeds survive;
  * a failure on one fleet is logged and counted, never aborting the
    remaining fleets;
  * output is aggregate-only (per-fleet counts and a pinned-release
    histogram) because the hourly workflow's logs are world-readable —
    this is a public repo. Per-device uuid lines need --verbose, which
    must stay out of CI.

Requires a balena API token in $BALENA_TOKEN (the same secret the
deploy workflows use). Examples:

    BALENA_TOKEN=... bin/balena_unpin_devices.py             # dry-run
    BALENA_TOKEN=... bin/balena_unpin_devices.py --apply
    BALENA_TOKEN=... bin/balena_unpin_devices.py \
        --fleet screenly_ose/anthias-pi4 --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any

API_BASE = 'https://api.balena-cloud.com/v7'

# Keep in sync with FLEET_DEVICE_TYPE in bin/balena_fleet_maintenance.py.
FLEETS = [
    'screenly_ose/anthias-pi2',
    'screenly_ose/anthias-pi3',
    'screenly_ose/anthias-pi3-64',
    'screenly_ose/anthias-pi4',
    'screenly_ose/anthias-pi5',
    'screenly_ose/anthias-x86',
    'screenly_ose/anthias-rockpi4',
]

KEEP_PINNED_TAG = 'anthias_keep_pinned'

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
) -> Any:
    """One balena API call. Returns parsed JSON (None for an empty or
    non-JSON body); raises HTTPError/URLError/OSError on failure."""
    url = f'{API_BASE}/{resource}'
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
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    try:
        return json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError:
        # Filtered PATCH returns a plain-text "OK" body.
        return None


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


def report_fleet(
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


def main() -> int:
    ap = argparse.ArgumentParser(
        description='Unpin every pinned device across the Anthias '
        'balena fleets.'
    )
    ap.add_argument(
        '--fleet',
        action='append',
        dest='fleets',
        metavar='SLUG',
        help='fleet slug, e.g. screenly_ose/anthias-pi4 (repeatable; '
        'default: all anthias fleets)',
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
    print(f'== balena release unpin ({mode}) ==')

    totals = {'pinned': 0, 'kept': 0, 'unpinned': 0, 'failed': 0}
    for fleet in fleets:
        try:
            devices = list_pinned_devices(token, fleet)
        except OSError as exc:
            print(
                f'\n# {fleet}: device listing failed: {exc}',
                file=sys.stderr,
            )
            totals['failed'] += 1
            continue
        kept = sum(1 for dev in devices if is_keep_pinned(dev))
        totals['pinned'] += len(devices)
        totals['kept'] += kept
        print(f'\n# {fleet}: pinned={len(devices)} keep-tagged={kept}')
        report_fleet(devices, args.apply, args.verbose)
        if not args.apply or len(devices) == kept:
            continue
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
            continue
        except OSError as exc:
            print(f'  ! bulk unpin failed: {exc}', file=sys.stderr)
            totals['failed'] += 1
            continue
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

    print(
        f'\n== summary: pinned={totals["pinned"]} kept={totals["kept"]} '
        f'unpinned={totals["unpinned"]} failed={totals["failed"]} =='
    )
    if not args.apply:
        print('Dry-run only. Re-run with --apply to make changes.')
    return 1 if totals['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
