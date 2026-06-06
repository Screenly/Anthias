#!/usr/bin/env python3
"""Staged balenaOS upgrade + release-unpin roller for the Anthias fleets.

A lot of field devices were flashed from pre-#2098 images that pinned them
to the downloaded release, and many sit on years-old balenaOS / supervisor
versions. This rolls them forward in tranches: per device it unpins the
release (so it tracks the fleet's latest stable) and starts a host OS update
to the latest balenaOS (which also carries the matching supervisor).

It is deliberately cautious:

  * dry-run by default — nothing mutates without --apply;
  * online-only by default — os-update needs the device reachable;
  * tranche-sized — process only --percent of the *remaining* eligible
    population per run (default 1%), so you start at 1%, watch, then re-run
    with a larger --percent as confidence grows;
  * resumable — successfully-processed devices are recorded in a local state
    file and excluded next run, and tagged in balenaCloud for visibility;
  * fault-tolerant — one device failing (e.g. an OS too old for a single-hop
    HUP) is logged and skipped, never aborting the run.

Requires the `balena` CLI (logged in) and `jq` is NOT needed. Examples:

    # See what a 1% tranche across every fleet would do (no changes):
    bin/balena_fleet_maintenance.py --all

    # Actually roll a 1% tranche of the Pi 4 fleet:
    bin/balena_fleet_maintenance.py --fleet screenly_ose/anthias-pi4 --apply

    # Once 1% looks healthy, widen the next tranche:
    bin/balena_fleet_maintenance.py --all --percent 10 --apply

Selection note: balenaCloud does not expand the per-device pin flag in the
bulk device list, so the eligible population is defined by *OS version*
(devices not already on the target balenaOS). `track-fleet` is then applied
to every device in the tranche — it is idempotent, so devices that are
already tracking the fleet are unaffected. Devices already on the target OS
but still pinned are out of scope here; unpin them with a targeted run.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

# fleet slug -> balenaOS device-type slug (matches the matrix in
# .github/workflows/build-balena-disk-image.yaml).
FLEET_DEVICE_TYPE = {
    'screenly_ose/anthias-pi2': 'raspberry-pi2',
    'screenly_ose/anthias-pi3': 'raspberrypi3',
    'screenly_ose/anthias-pi3-64': 'raspberrypi3-64',
    'screenly_ose/anthias-pi4': 'raspberrypi4-64',
    'screenly_ose/anthias-pi5': 'raspberrypi5',
    'screenly_ose/anthias-x86': 'generic-amd64',
    'screenly_ose/anthias-rockpi4': 'rockpi-4b-rk3399',
}

MAINTENANCE_TAG = 'anthias_maintenance'


def run_balena(
    args: list[str], parse_json: bool = False
) -> tuple[bool, Any, str]:
    """Run a balena CLI command. Returns (ok, stdout, stderr).

    balena prints Node/CLI warnings to stderr, so stdout stays clean for
    --json parsing. Never raises — callers decide how to handle failure.
    """
    try:
        proc = subprocess.run(
            ['balena', *args],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, '', str(exc)
    out = proc.stdout.strip()
    if parse_json:
        if proc.returncode != 0:
            return False, out, proc.stderr.strip()
        try:
            return True, json.loads(out), proc.stderr.strip()
        except json.JSONDecodeError as exc:
            return False, out, f'JSON parse error: {exc}'
    return proc.returncode == 0, out, proc.stderr.strip()


def parse_os_version(raw: str) -> tuple[int, int, int, int]:
    """Normalize 'balenaOS 6.1.24+rev4' / 'v6.12.3+rev4' to a comparable
    tuple (major, minor, patch, rev). Unparseable -> all zeros (treated as
    oldest)."""
    s = raw.replace('balenaOS', '').strip().lstrip('v')
    rev = 0
    if '+rev' in s:
        s, _, rev_s = s.partition('+rev')
        rev_digits = ''.join(c for c in rev_s if c.isdigit())
        rev = int(rev_digits) if rev_digits else 0
    parts = s.split('.')
    nums = []
    for p in parts[:3]:
        digits = ''.join(c for c in p if c.isdigit())
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2], rev)


def normalize_os(raw: str) -> str:
    """'balenaOS 6.1.24+rev4' -> '6.1.24+rev4' (the form os-update wants)."""
    return raw.replace('balenaOS', '').strip().lstrip('v')


def latest_os_version(device_type: str) -> str | None:
    """Newest non-draft balenaOS version for a device type, e.g.
    '6.12.3+rev4'. Returns None if the lookup fails."""
    ok, out, err = run_balena(['os', 'versions', device_type])
    if not ok:
        print(
            f'  ! could not list OS versions for {device_type}: {err}',
            file=sys.stderr,
        )
        return None
    versions: list[str] = [
        line.strip().lstrip('v')
        for line in out.splitlines()
        if line.strip().lstrip('v')[:1].isdigit()
    ]
    if not versions:
        return None
    # `balena os versions` already prints newest-first, but sort defensively.
    versions.sort(key=parse_os_version, reverse=True)
    return versions[0]


@dataclass
class Device:
    uuid: str
    os_version: str
    supervisor_version: str
    is_online: bool


def list_devices(fleet: str) -> list[Device]:
    ok, data, err = run_balena(
        ['device', 'list', '--fleet', fleet, '--json'], parse_json=True
    )
    if not ok:
        print(
            f'  ! could not list devices for {fleet}: {err}', file=sys.stderr
        )
        return []
    devices = []
    for d in data:
        devices.append(
            Device(
                uuid=d.get('uuid', ''),
                os_version=d.get('os_version') or '',
                supervisor_version=d.get('supervisor_version') or '',
                is_online=bool(d.get('is_online')),
            )
        )
    return devices


def load_done(state_path: str) -> set[str]:
    """UUIDs that were fully processed in a prior run (state file is TSV:
    uuid<TAB>fleet<TAB>timestamp<TAB>status)."""
    done = set()
    try:
        with open(state_path, encoding='utf-8') as fh:
            for line in fh:
                parts = line.rstrip('\n').split('\t')
                if len(parts) >= 4 and parts[3] == 'ok':
                    done.add(parts[0])
    except FileNotFoundError:
        pass
    return done


def append_state(
    state_path: str, uuid: str, fleet: str, ts: str, status: str, detail: str
) -> None:
    with open(state_path, 'a', encoding='utf-8') as fh:
        fh.write(f'{uuid}\t{fleet}\t{ts}\t{status}\t{detail}\n')


def select_tranche(
    devices: list[Device],
    target_os: str,
    done: set[str],
    include_offline: bool,
    order: str,
    percent: float,
    count: int | None,
) -> tuple[list[Device], int]:
    eligible = [
        d
        for d in devices
        if d.uuid
        and d.uuid not in done
        and (include_offline or d.is_online)
        and normalize_os(d.os_version) != target_os
    ]
    if order == 'oldest':
        eligible.sort(key=lambda d: parse_os_version(d.os_version))
    elif order == 'newest':
        eligible.sort(
            key=lambda d: parse_os_version(d.os_version), reverse=True
        )
    else:  # 'uuid' — stable, representative sample
        eligible.sort(key=lambda d: d.uuid)

    if not eligible:
        return [], 0
    if count is not None:
        size = min(count, len(eligible))
    else:
        size = max(1, math.ceil(len(eligible) * percent / 100.0))
    return eligible[:size], len(eligible)


def process_device(
    dev: Device,
    fleet: str,
    target_os: str,
    do_unpin: bool,
    do_os_update: bool,
    do_tag: bool,
    ts: str,
) -> tuple[bool, str]:
    """Apply the enabled actions to one device. Returns (ok, detail)."""
    notes = []
    ok = True

    if do_unpin:
        u_ok, _, u_err = run_balena(['device', 'track-fleet', dev.uuid])
        notes.append('unpin:ok' if u_ok else f'unpin:FAIL({u_err[:80]})')
        ok = ok and u_ok

    if do_os_update:
        o_ok, _, o_err = run_balena(
            ['device', 'os-update', dev.uuid, '--version', target_os, '-y']
        )
        notes.append('os:ok' if o_ok else f'os:FAIL({o_err[:80]})')
        ok = ok and o_ok

    if do_tag and ok:
        # Best-effort visibility marker; not fatal if it fails.
        run_balena(['tag', 'set', MAINTENANCE_TAG, ts, '--device', dev.uuid])

    return ok, '; '.join(notes)


def main() -> int:
    ap = argparse.ArgumentParser(
        description='Staged balenaOS upgrade + release-unpin roller.'
    )
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '--fleet',
        action='append',
        dest='fleets',
        help='fleet slug, e.g. screenly_ose/anthias-pi4 (repeatable)',
    )
    group.add_argument(
        '--all', action='store_true', help='operate on all anthias fleets'
    )
    ap.add_argument(
        '--percent',
        type=float,
        default=1.0,
        help='tranche size as %% of the remaining eligible '
        'population (default 1)',
    )
    ap.add_argument(
        '--count',
        type=int,
        default=None,
        help='absolute tranche size; overrides --percent',
    )
    ap.add_argument(
        '--os-version',
        default=None,
        help='target balenaOS version (default: latest per device type)',
    )
    ap.add_argument(
        '--order',
        choices=['uuid', 'oldest', 'newest'],
        default='uuid',
        help='tranche selection order (default uuid = representative sample)',
    )
    ap.add_argument(
        '--no-unpin',
        action='store_true',
        help='skip release unpin (track-fleet)',
    )
    ap.add_argument(
        '--no-os-update', action='store_true', help='skip the host OS update'
    )
    ap.add_argument(
        '--no-tag',
        action='store_true',
        help='skip the balenaCloud visibility tag',
    )
    ap.add_argument(
        '--include-offline',
        action='store_true',
        help='include offline devices (os-update needs online)',
    )
    ap.add_argument(
        '--state',
        default='./.balena-maint-state.tsv',
        help='resumable state file (default ./.balena-maint-state.tsv)',
    )
    ap.add_argument(
        '--sleep',
        type=float,
        default=2.0,
        help='seconds between devices (default 2)',
    )
    ap.add_argument(
        '--apply',
        action='store_true',
        help='actually perform changes (default: dry-run)',
    )
    args = ap.parse_args()

    fleets = list(FLEET_DEVICE_TYPE) if args.all else args.fleets
    do_unpin = not args.no_unpin
    do_os_update = not args.no_os_update
    do_tag = not args.no_tag
    if not do_unpin and not do_os_update:
        print('Nothing to do: both --no-unpin and --no-os-update set.')
        return 2

    ts = time.strftime('%Y-%m-%d')
    done = load_done(args.state)
    mode = 'APPLY' if args.apply else 'DRY-RUN'
    print(f'== balena fleet maintenance ({mode}) ==')
    print(
        f'actions: unpin={do_unpin} os-update={do_os_update} '
        f'tag={do_tag} | online-only={not args.include_offline} | '
        f'order={args.order} | already-done={len(done)}'
    )

    totals = {'planned': 0, 'ok': 0, 'failed': 0}
    for fleet in fleets:
        device_type = FLEET_DEVICE_TYPE.get(fleet)
        if device_type is None:
            print(f'\n# {fleet}: unknown fleet, skipping', file=sys.stderr)
            continue
        target_os = args.os_version or latest_os_version(device_type)
        if not target_os:
            print(
                f'\n# {fleet}: cannot resolve target OS, skipping',
                file=sys.stderr,
            )
            continue

        devices = list_devices(fleet)
        tranche, n_eligible = select_tranche(
            devices,
            target_os,
            done,
            args.include_offline,
            args.order,
            args.percent,
            args.count,
        )
        if not tranche:
            print(f'\n# {fleet} (target {target_os}): nothing eligible')
            continue
        print(f'\n# {fleet} (target balenaOS {target_os})')
        print(
            f'  devices={len(devices)} eligible={n_eligible} '
            f'tranche={len(tranche)}'
        )

        for dev in tranche:
            totals['planned'] += 1
            cur = normalize_os(dev.os_version) or '?'
            online = 'online' if dev.is_online else 'OFFLINE'
            if not args.apply:
                print(
                    f'  [plan] {dev.uuid[:12]} {online} '
                    f'os {cur} -> {target_os}'
                    f'{" +unpin" if do_unpin else ""}'
                )
                continue
            ok, detail = process_device(
                dev, fleet, target_os, do_unpin, do_os_update, do_tag, ts
            )
            status = 'ok' if ok else 'failed'
            totals['ok' if ok else 'failed'] += 1
            append_state(args.state, dev.uuid, fleet, ts, status, detail)
            print(
                f'  [{status}] {dev.uuid[:12]} {online} {cur} -> '
                f'{target_os} | {detail}'
            )
            if args.sleep:
                time.sleep(args.sleep)

    print(
        f'\n== summary: planned={totals["planned"]} '
        f'ok={totals["ok"]} failed={totals["failed"]} =='
    )
    if not args.apply:
        print('Dry-run only. Re-run with --apply to make changes.')
    return 1 if totals['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
