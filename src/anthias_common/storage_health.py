"""Storage health for the card Anthias runs from.

Under-voltage has one kernel sensor to read and the whole of
:mod:`anthias_common.undervoltage` is a wrapper around it. Failing
storage has no such sensor. SD cards carry no health register at all
-- the SD specification has no SMART equivalent, and the controller
inside the card hides its own remapping and wear from the host by
design. A card that is dying announces it by returning bad data, not
by saying so.

So this module assembles a verdict out of four things the kernel does
expose. All four are readable from inside an unprivileged container,
which is what keeps one code path working on docker-compose installs
and on Balena fleets, where there is no host agent to ask:

* **ext4's error counters**, ``/sys/fs/ext4/<dev>/errors_count`` and
  its ``first_error_*``/``last_error_*`` companions. ext4 keeps these
  in the superblock rather than in memory, so unlike the firmware
  throttle bits behind under-voltage they are a genuinely durable
  record: they survive reboots and are cleared only by ``fsck`` or
  ``tune2fs``. A card that has begun handing back corrupt blocks
  shows up here as a rising count long before it stops mounting.
* **Whether the filesystem still takes writes.** Raspberry Pi OS
  mounts the root filesystem ``errors=remount-ro``, so the endgame of
  a dying card is a read-only root. The player keeps showing content
  and the web UI keeps loading, while every upload, schedule edit and
  setting change silently fails to stick. Error counters cannot see
  that state, because once the filesystem is read-only nothing is
  going wrong any more -- nothing is being written. It has to be
  tested directly, so we write a small file and read it back.
* **eMMC wear registers**, ``life_time`` and ``pre_eol_info``. Only
  the compute modules and a few industrial boards have them; a plain
  SD card does not. Where they exist they are the one true
  before-it-fails signal on the whole device, so they are worth
  reading even though most of the fleet will not have them.
* **Which card this is**, from the MMC/SD identification registers.
  Not a health signal. It is here because "SanDisk SC32G, made
  2019-03" turns a support conversation about an unknown card into a
  short one, and because knowing whether the media is SD, eMMC or an
  SSD is what lets the UI give advice that fits the hardware.

The device is resolved at runtime from ``/proc/self/mountinfo``
rather than assumed to be ``mmcblk0p2``: inside the container the
data directory is a bind mount, on Balena it is a named volume on a
different partition entirely, and on x86 it is an SSD. Following the
mount to its device number and then to ``/sys/dev/block`` gets the
right answer everywhere without a special case per platform.

What is deliberately *not* here is the kernel ring buffer. ``dmesg``
carries the richest evidence by far (``blk_update_request: I/O
error``, ``mmc0: timed out sending r/w cmd``), but it is not readable
from an unprivileged container, so building on it would mean a
feature that works on docker-compose installs and silently does
nothing on Balena. It is captured by ``bin/collect_debug.sh``
instead, which runs on the host.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

MOUNTINFO_PATH = '/proc/self/mountinfo'
UPTIME_PATH = '/proc/uptime'
BOOT_ID_PATH = '/proc/sys/kernel/random/boot_id'

# Kernel-global sysfs, so all three read identically inside a
# container with no extra mounts, devices or capabilities.
SYS_DEV_BLOCK = '/sys/dev/block'
SYS_BLOCK = '/sys/block'
SYS_FS_EXT4 = '/sys/fs/ext4'

# Redis key holding the latch. ``host:`` prefix matches the existing
# convention for host-shape facts (see anthias_common.board).
REDIS_KEY = 'host:storage_health'

# Written into the data directory by the write check and removed
# again in the same call. Leading dot so that even in the window it
# exists, or if a crash leaves it behind, it stays out of a directory
# listing an operator is reading.
CANARY_FILENAME = '.anthias-write-check'

# One page. Big enough that a short read or a zero-filled read is
# unambiguous, small enough that writing it every quarter of an hour
# is irrelevant next to what SQLite and the logs already do.
CANARY_PAYLOAD_SIZE = 4096

# Verdicts, worst first. ``full`` is a genuinely different problem
# with genuinely different advice, and lumping it in with a failing
# card would send an operator out to buy hardware they don't need.
STATUS_FAILING = 'failing'
STATUS_FULL = 'full'
STATUS_ERRORS = 'errors'
STATUS_WEAR = 'wear'
STATUS_OK = 'ok'
STATUS_UNKNOWN = 'unknown'

# Why a write check failed, in the order the UI cares about.
REASON_READ_ONLY = 'read_only'
REASON_NO_SPACE = 'no_space'
REASON_IO_ERROR = 'io_error'
REASON_CORRUPT = 'corrupt'
REASON_ERROR = 'error'

# eMMC PRE_EOL_INFO (EXT_CSD byte 267). 0x01 is normal; 0x02 means
# 80% of the reserved blocks are consumed and 0x03 means 90%.
PRE_EOL_LABELS = {0x01: 'normal', 0x02: 'warning', 0x03: 'urgent'}

# DEVICE_LIFE_TIME_EST (EXT_CSD 268/269) in 10% bands: 0x01 is 0-10%
# used, 0x0a is 90-100%, 0x0b means the estimate has been exceeded.
# Warn from 80% on, which leaves time to plan a swap rather than
# discover it during a support call.
WEAR_WARN_PCT = 80

# MMC/SD manufacturer IDs, best effort. The list is not authoritative
# -- the SD Association does not publish one -- so an ID that is not
# here renders as its raw hex rather than being guessed at.
MANUFACTURER_IDS = {
    0x01: 'Panasonic',
    0x02: 'Toshiba/Kioxia',
    0x03: 'SanDisk',
    0x1B: 'Samsung',
    0x1D: 'ADATA',
    0x27: 'Phison',
    0x28: 'Lexar',
    0x31: 'Silicon Power',
    0x41: 'Kingston',
    0x74: 'Transcend',
    0x76: 'Patriot',
    0x82: 'Sony',
    0x9C: 'Angelbird/Hoodman',
}


def _read_text(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> int | None:
    raw = _read_text(path)
    if raw is None:
        return None
    try:
        return int(raw, 0)
    except ValueError:
        return None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


def _epoch_to_iso(value: int | None) -> str | None:
    """Superblock timestamp (epoch seconds) to ISO, or ``None``.

    ext4 writes 0 when it has never recorded an error. A Pi has no
    real-time clock, so a timestamp from before NTP first synced can
    be arbitrarily wrong; it is still shown, because a wrong date an
    operator can see beats a field silently blanked.
    """
    if not value:
        return None
    try:
        return datetime.fromtimestamp(value, UTC).isoformat(timespec='seconds')
    except (OverflowError, OSError, ValueError):
        return None


def get_boot_id() -> str | None:
    return _read_text(BOOT_ID_PATH)


def get_boot_time() -> float | None:
    """Wall-clock epoch seconds at which the host booted.

    Derived from ``/proc/uptime``, which a container sees as the
    host's uptime, so this works without a host agent. Used to decide
    whether a superblock error timestamp belongs to the current boot.
    """
    raw = _read_text(UPTIME_PATH)
    if not raw:
        return None
    try:
        uptime = float(raw.split()[0])
    except (IndexError, ValueError):
        return None
    return time.time() - uptime


def default_data_dir() -> str:
    """The directory whose filesystem we care about.

    Mirrors ``anthias_server.settings.get_configdir()`` without
    importing it -- ``anthias_common`` must not depend on the server
    package. Server-side callers pass the real path in; this is the
    fallback for the viewer and for tooling.
    """
    return os.path.join(os.getenv('HOME') or '/data', '.anthias')


def _unescape(field: str) -> str:
    """Decode mountinfo's octal escaping of space, tab, newline and \\."""
    if '\\' not in field:
        return field

    out: list[str] = []
    i = 0
    while i < len(field):
        if field[i] == '\\' and i + 3 < len(field):
            chunk = field[i + 1 : i + 4]
            if len(chunk) == 3 and all(c in '01234567' for c in chunk):
                out.append(chr(int(chunk, 8)))
                i += 4
                continue
        out.append(field[i])
        i += 1
    return ''.join(out)


def _is_within(mount_point: str, target: str) -> bool:
    """Whether ``target`` sits under ``mount_point``.

    Compares whole path components so ``/data`` does not appear to
    contain ``/database``.
    """
    if mount_point == '/':
        return True
    return target == mount_point or target.startswith(mount_point + '/')


def find_mount(target: str) -> dict[str, Any] | None:
    """The mountinfo entry whose filesystem backs ``target``.

    Picks the longest matching mount point, and among equal-length
    matches the last one, which is how the kernel resolves an
    over-mount: later entries shadow earlier ones.
    """
    try:
        with open(MOUNTINFO_PATH) as f:
            lines = f.readlines()
    except OSError:
        return None

    target = os.path.abspath(target)
    best: dict[str, Any] | None = None

    for line in lines:
        fields = line.split()
        try:
            separator = fields.index('-')
        except ValueError:
            continue
        if len(fields) < separator + 3 or separator < 6:
            continue

        mount_point = _unescape(fields[4])
        if not _is_within(mount_point, target):
            continue
        if best is not None and len(mount_point) < len(best['mount_point']):
            continue

        best = {
            'mount_point': mount_point,
            'major_minor': fields[2],
            'mount_options': fields[5],
            'fstype': _unescape(fields[separator + 1]),
            'source': _unescape(fields[separator + 2]),
            'super_options': fields[separator + 3]
            if len(fields) > separator + 3
            else '',
        }

    return best


def is_read_only(mount: dict[str, Any]) -> bool:
    """Whether the filesystem currently refuses writes.

    Both option fields are checked, and the superblock one is the
    load-bearing half. When ext4 hits an error under
    ``errors=remount-ro`` it marks the *superblock* read-only; the
    per-mount options can still read ``rw``, so looking only at field
    5 would miss precisely the failure this exists to catch.
    """
    for field in ('mount_options', 'super_options'):
        options = str(mount.get(field) or '').split(',')
        if 'ro' in options:
            return True
    return False


def resolve_device(major_minor: str) -> dict[str, str | None]:
    """Kernel device name and parent disk for a ``maj:min`` pair.

    ``device`` is what ext4 keys its sysfs directory on (the
    partition, e.g. ``mmcblk0p2``); ``disk`` is the whole device the
    MMC/SD registers hang off (``mmcblk0``). They differ for every
    real install and coincide only for an unpartitioned device.

    Both are ``None`` for a virtual filesystem such as the container's
    own overlayfs, which has a device number but no entry under
    ``/sys/dev/block``.
    """
    link = os.path.join(SYS_DEV_BLOCK, major_minor)
    try:
        sys_path = os.path.realpath(link)
    except OSError:
        return {'device': None, 'disk': None}
    if not os.path.isdir(sys_path):
        return {'device': None, 'disk': None}

    device = os.path.basename(sys_path)
    # A partition carries a ``partition`` attribute and lives inside
    # its disk's directory; a whole device has neither.
    if os.path.exists(os.path.join(sys_path, 'partition')):
        disk: str | None = os.path.basename(os.path.dirname(sys_path))
    else:
        disk = device

    return {'device': device, 'disk': disk}


def read_ext4_errors(device: str | None) -> dict[str, Any]:
    """ext4's superblock error counters for ``device``.

    ``supported`` is false for any other filesystem (f2fs, btrfs, or
    an overlay we failed to resolve). The write check still applies
    there, so the caller degrades to that rather than going silent.
    """
    blank: dict[str, Any] = {
        'supported': False,
        'count': 0,
        'first_time': None,
        'last_time': None,
        'last_time_epoch': None,
        'last_function': None,
        'lifetime_written_kb': None,
    }
    if not device:
        return blank

    base = os.path.join(SYS_FS_EXT4, device)
    count = _read_int(os.path.join(base, 'errors_count'))
    if count is None:
        return blank

    last_epoch = _read_int(os.path.join(base, 'last_error_time'))
    return {
        'supported': True,
        'count': count,
        'first_time': _epoch_to_iso(
            _read_int(os.path.join(base, 'first_error_time'))
        ),
        'last_time': _epoch_to_iso(last_epoch),
        'last_time_epoch': last_epoch,
        'last_function': _read_text(os.path.join(base, 'last_error_func')),
        # Total kilobytes ever written to this filesystem, also from
        # the superblock. Not an error signal; it is the closest thing
        # to a wear figure a plain SD card offers, and it is what
        # makes "this card has had 4 TB written to it" sayable.
        'lifetime_written_kb': _read_int(
            os.path.join(base, 'lifetime_write_kbytes')
        ),
    }


def _parse_life_time(raw: str | None) -> int | None:
    """``life_time`` ("0x02 0x01") to a worst-case percentage used.

    The register reports two estimates, one per SLC/MLC area, each in
    a 10% band. We report the worse of the two: a device whose SLC
    area is spent is at end of life whatever the other number says.
    """
    if not raw:
        return None

    values = []
    for token in raw.split():
        try:
            value = int(token, 16)
        except ValueError:
            continue
        # 0x00 means "not defined" rather than "no wear".
        if value:
            values.append(value)
    if not values:
        return None

    worst = max(values)
    # 0x0b: the estimate has been exceeded, i.e. past 100%.
    return 100 if worst >= 0x0B else min(worst * 10, 100)


def read_media_info(disk: str | None) -> dict[str, Any]:
    """Media kind, identity and eMMC wear for a whole device."""
    info: dict[str, Any] = {
        'kind': 'unknown',
        'name': None,
        'manufacturer': None,
        'manufactured': None,
        'wear_pct': None,
        'pre_eol': None,
    }
    if not disk:
        return info

    device_dir = os.path.join(SYS_BLOCK, disk, 'device')

    # ``type`` is "SD" or "MMC" for the mmc bus and absent elsewhere.
    # eMMC is soldered down, so the advice for it is "plan a board
    # swap", not "replace the card" -- worth telling apart.
    card_type = _read_text(os.path.join(device_dir, 'type'))
    if card_type == 'SD':
        info['kind'] = 'sd'
    elif card_type == 'MMC':
        info['kind'] = 'emmc'
    elif disk.startswith(('nvme', 'sd')):
        info['kind'] = 'disk'

    if info['kind'] == 'unknown':
        return info

    info['name'] = _read_text(os.path.join(device_dir, 'name'))

    manfid = _read_int(os.path.join(device_dir, 'manfid'))
    if manfid is not None:
        info['manufacturer'] = MANUFACTURER_IDS.get(
            manfid, f'Unknown (0x{manfid:02x})'
        )

    # The MMC date register is MM/YYYY and has no day, so it stays a
    # string rather than being forced into a date the card never gave.
    info['manufactured'] = _read_text(os.path.join(device_dir, 'date'))

    info['wear_pct'] = _parse_life_time(
        _read_text(os.path.join(device_dir, 'life_time'))
    )
    pre_eol = _read_int(os.path.join(device_dir, 'pre_eol_info'))
    if pre_eol is not None:
        info['pre_eol'] = PRE_EOL_LABELS.get(pre_eol)

    return info


def _classify(exc: OSError) -> str:
    if exc.errno == errno.EROFS:
        return REASON_READ_ONLY
    if exc.errno in (errno.ENOSPC, errno.EDQUOT):
        return REASON_NO_SPACE
    if exc.errno == errno.EIO:
        return REASON_IO_ERROR
    return REASON_ERROR


def run_write_check(data_dir: str) -> dict[str, Any]:
    """Write a file, flush it to the card, read it back, compare.

    This is the only active probe in the module, and it exists
    because the passive signals cannot see the failure that matters
    most. Once ext4 has remounted read-only the error counters stop
    moving, the card reads back fine, and the only visible symptom is
    that the operator's changes quietly do not persist.

    The payload is unique per run and non-uniform, so a stale copy
    served from an earlier check and a block that reads back as zeros
    both fail the comparison rather than passing it.

    ``fsync`` is what pushes the data past the page cache to the
    card, and it is also the measurement: on a card whose controller
    has started retrying internally it goes from single-digit
    milliseconds to seconds. ``POSIX_FADV_DONTNEED`` after it is what
    stops the read-back being answered out of the page cache, which
    would make this a test of RAM. It is advisory, so the check
    remains a strong positive signal (a failure is real) and a weaker
    negative one (a pass does not prove every block is good).

    The file is removed afterwards. ``backup_helper`` tars the whole
    of ``~/.anthias``, so a file left lying there would ride along in
    every backup an operator downloads, and turn up in debug bundles
    as something a support engineer has to recognise and dismiss. A
    probe should not leave litter in the thing it is inspecting.
    """
    path = os.path.join(data_dir, CANARY_FILENAME)
    seed = f'anthias-write-check {_now_iso()} {os.getpid()}'.encode()
    digest = hashlib.sha256(seed).digest()
    payload = (digest * (CANARY_PAYLOAD_SIZE // len(digest) + 1))[
        :CANARY_PAYLOAD_SIZE
    ]

    result: dict[str, Any] = {
        'ok': False,
        'reason': None,
        'errno': None,
        'fsync_ms': None,
        'checked_at': _now_iso(),
    }

    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, payload)
            started = time.monotonic()
            os.fsync(fd)
            result['fsync_ms'] = round((time.monotonic() - started) * 1000, 1)
            try:
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            except (AttributeError, OSError):
                # Not fatal: the read-back may come from cache, so we
                # lose some sensitivity but no correctness.
                pass
        finally:
            os.close(fd)

        with open(path, 'rb') as f:
            read_back = f.read()
    except OSError as exc:
        result['reason'] = _classify(exc)
        result['errno'] = errno.errorcode.get(exc.errno or 0, str(exc.errno))
        return result
    finally:
        try:
            os.unlink(path)
        except OSError:
            # Nothing was created (the open failed), or the filesystem
            # went read-only between writing and removing. Either way
            # the verdict above stands; a leftover 4 KB file is not
            # worth reporting as a second failure.
            pass

    if read_back != payload:
        result['reason'] = REASON_CORRUPT
        return result

    result['ok'] = True
    return result


def _blank_latch() -> dict[str, Any]:
    return {
        'errors_baseline': None,
        'write_ok': None,
        'write_reason': None,
        'write_failed_since_boot': False,
        'write_fail_count': 0,
        'first_write_fail': None,
        'last_write_fail': None,
        'last_check': None,
        'fsync_ms': None,
    }


def _load_latch(redis_client: Any, boot_id: str | None) -> dict[str, Any]:
    """Read the stored latch, discarding it if it predates this boot.

    Same reasoning as the under-voltage latch: Redis persists to a
    volume, so without the boot-id check a device that had a bad hour
    last week would still be warning after a reboot and a fresh card.

    Note this only resets the *observed-by-us* half. ext4's own error
    count lives in the superblock and deliberately survives, because
    a card that corrupted data before the last reboot is still the
    same card.
    """
    try:
        raw = redis_client.get(REDIS_KEY)
    except Exception:
        return _blank_latch()

    if not raw:
        return _blank_latch()
    if isinstance(raw, bytes):
        try:
            raw = raw.decode('utf-8')
        except UnicodeDecodeError:
            return _blank_latch()

    try:
        stored = json.loads(raw)
    except (ValueError, TypeError):
        return _blank_latch()
    if not isinstance(stored, dict):
        return _blank_latch()
    if stored.get('boot_id') != boot_id:
        return _blank_latch()

    latch = _blank_latch()
    for key in latch:
        if key in stored:
            latch[key] = stored[key]
    return latch


def _save_latch(
    redis_client: Any, latch: dict[str, Any], boot_id: str | None
) -> None:
    payload = dict(latch)
    payload['boot_id'] = boot_id
    try:
        redis_client.set(REDIS_KEY, json.dumps(payload))
    except Exception:
        # Losing the latch costs us the history, not the live
        # reading; the caller still gets an accurate verdict. Logged
        # rather than swallowed, and at the same level as the
        # under-voltage latch for the same reason: a silently lost
        # latch makes the banner look like it de-escalated on its own.
        logger.warning(
            'Could not persist the storage-health latch to Redis.',
            exc_info=True,
        )


def probe(data_dir: str | None = None) -> dict[str, Any]:
    """Everything readable without writing anything or touching Redis."""
    if data_dir is None:
        data_dir = default_data_dir()

    mount = find_mount(data_dir)
    if mount is None:
        return {
            'supported': False,
            'mount_point': None,
            'fstype': None,
            'device': None,
            'disk': None,
            'read_only': False,
            'errors': read_ext4_errors(None),
            'media': read_media_info(None),
        }

    resolved = resolve_device(mount['major_minor'])
    return {
        'supported': True,
        'mount_point': mount['mount_point'],
        'fstype': mount['fstype'],
        'device': resolved['device'],
        'disk': resolved['disk'],
        'read_only': is_read_only(mount),
        'errors': read_ext4_errors(resolved['device']),
        'media': read_media_info(resolved['disk']),
    }


def _classify_status(state: dict[str, Any]) -> str:
    """Reduce the collected signals to the one verdict the UI shows.

    Ordered worst-first, and the order is the point: a card that is
    read-only right now needs replacing today, while a nonzero
    historical error count is a "keep an eye on this". Showing the
    milder of two true statements would be the wrong call, so the
    first match wins.
    """
    if not state['supported']:
        return STATUS_UNKNOWN

    if state['read_only']:
        return STATUS_FAILING
    if state['write_ok'] is False:
        if state['write_reason'] == REASON_NO_SPACE:
            return STATUS_FULL
        return STATUS_FAILING

    # New errors while we were watching, or a superblock timestamp
    # that falls inside this boot. The second catches errors from
    # before celery started, which the baseline alone would miss --
    # mount-time errors on a bad card land in exactly that window.
    if state['errors_new'] > 0 or state['errors_this_boot']:
        return STATUS_FAILING

    if state['write_failed_since_boot']:
        return STATUS_FAILING
    if state['errors_count'] > 0:
        return STATUS_ERRORS

    wear = state['media']['wear_pct']
    if state['media']['pre_eol'] in ('warning', 'urgent'):
        return STATUS_WEAR
    if wear is not None and wear >= WEAR_WARN_PCT:
        return STATUS_WEAR

    return STATUS_OK


def _assemble(
    facts: dict[str, Any],
    latch: dict[str, Any],
    boot_time: float | None = None,
) -> dict[str, Any]:
    errors = facts['errors']
    baseline = latch['errors_baseline']

    last_epoch = errors['last_time_epoch']
    errors_this_boot = bool(
        errors['supported']
        and last_epoch
        and boot_time is not None
        # A minute of slack: the superblock timestamp and our
        # uptime arithmetic come from different clocks, and a Pi's
        # clock jumps when NTP first syncs.
        and last_epoch >= boot_time - 60
    )

    state: dict[str, Any] = {
        'supported': facts['supported'],
        'mount_point': facts['mount_point'],
        'fstype': facts['fstype'],
        'device': facts['device'],
        'disk': facts['disk'],
        'read_only': facts['read_only'],
        'media': facts['media'],
        'error_stats_supported': errors['supported'],
        'errors_count': errors['count'],
        'errors_new': max(0, errors['count'] - baseline)
        if baseline is not None
        else 0,
        'errors_this_boot': errors_this_boot,
        'first_error': errors['first_time'],
        'last_error': errors['last_time'],
        'last_error_function': errors['last_function'],
        'lifetime_written_kb': errors['lifetime_written_kb'],
        'write_ok': latch['write_ok'],
        'write_reason': latch['write_reason'],
        'write_failed_since_boot': latch['write_failed_since_boot'],
        'write_fail_count': latch['write_fail_count'],
        'first_write_fail': latch['first_write_fail'],
        'last_write_fail': latch['last_write_fail'],
        'last_check': latch['last_check'],
        'fsync_ms': latch['fsync_ms'],
    }
    state['status'] = _classify_status(state)
    return state


def record_check(
    redis_client: Any,
    data_dir: str | None = None,
    write_check: bool = True,
    boot_id: str | None = None,
) -> dict[str, Any]:
    """Take a full reading, fold it into the latch, return the state.

    Called by the watcher. ``write_check`` is optional so the watcher
    can sample the cheap sysfs counters often and pay for an actual
    write only occasionally.
    """
    if data_dir is None:
        data_dir = default_data_dir()
    if boot_id is None:
        boot_id = get_boot_id()

    facts = probe(data_dir)
    latch = _load_latch(redis_client, boot_id)

    if facts['errors']['supported'] and latch['errors_baseline'] is None:
        latch['errors_baseline'] = facts['errors']['count']

    if write_check and facts['supported']:
        result = run_write_check(data_dir)
        latch['write_ok'] = result['ok']
        latch['write_reason'] = result['reason']
        latch['last_check'] = result['checked_at']
        if result['fsync_ms'] is not None:
            latch['fsync_ms'] = result['fsync_ms']
        if not result['ok']:
            # Latched the same way under-voltage latches a dip: a
            # write that failed and then succeeded is not a card that
            # is fine, it is a card that is starting to go.
            if not latch['write_failed_since_boot']:
                latch['write_failed_since_boot'] = True
                latch['first_write_fail'] = result['checked_at']
            latch['last_write_fail'] = result['checked_at']
            latch['write_fail_count'] = (latch['write_fail_count'] or 0) + 1

    _save_latch(redis_client, latch, boot_id)
    return _assemble(facts, latch, get_boot_time())


def get_state(
    redis_client: Any, data_dir: str | None = None
) -> dict[str, Any]:
    """Storage state for the UI and the API.

    Reads sysfs live and merges the latch, but never writes: a page
    render must not be able to block on ``fsync`` against a card that
    is already struggling, which is exactly when someone is loading
    the page.

    ``supported`` is false only when the data directory's filesystem
    could not be resolved at all. Callers must check it first -- every
    other field is at its "nothing wrong" value in that case and is
    indistinguishable from a healthy device.
    """
    facts = probe(data_dir)
    latch = _load_latch(redis_client, get_boot_id())
    return _assemble(facts, latch, get_boot_time())


def should_warn(state: dict[str, Any]) -> bool:
    """Whether to show the operator a warning."""
    return bool(state.get('supported')) and state.get('status') in (
        STATUS_FAILING,
        STATUS_FULL,
        STATUS_ERRORS,
        STATUS_WEAR,
    )
