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
* **Whether the filesystem still takes writes.** A filesystem mounted
  ``errors=remount-ro`` stops dead on the first error, and the player
  then keeps showing content and serving the web UI while every
  upload, schedule edit and setting change silently fails to stick.
  Error counters cannot see that state, because once the filesystem
  has stopped nothing is going wrong any more -- nothing is being
  written. It has to be tested directly, so we write a small file and
  read it back.

  Measured across the testbed fleet, this is *not* the Raspberry Pi
  case: every Pi's root filesystem reports ``Errors behavior:
  Continue`` in its superblock and carries no ``errors=`` mount
  option, so a dying card there does not go read-only. It keeps
  limping, and the error counters below are what rises. The Rock Pi 4
  is the opposite -- its fstab passes ``errors=remount-ro``, so it
  does stop. Both endings happen on the fleet, which is why both
  signals are read rather than picking whichever seemed dominant.
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

There is a fifth source that is *not* readable here: SMART, which is
where an x86 box keeps the wear figure that eMMC puts in a register.
Reading it needs an ioctl and therefore privilege this container does
not have, so ``anthias_common.smart`` has the privileged viewer sample
it and publish a Redis fact, and :func:`read_media_info` folds that
fact into the same ``wear_pct``/``pre_eol`` fields eMMC populates. The
UI and the API consequently need no separate SSD branch.

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

from anthias_common import smart

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

# Mount options that mean "this filesystem is refusing writes". See
# is_read_only for why there is more than one spelling.
READ_ONLY_OPTIONS = frozenset({'ro', 'emergency_ro', 'shutdown'})

# Why a write check failed, in the order the UI cares about.
REASON_READ_ONLY = 'read_only'
REASON_NO_SPACE = 'no_space'
REASON_IO_ERROR = 'io_error'
REASON_CORRUPT = 'corrupt'
REASON_ERROR = 'error'
# Not a storage fault: the data directory does not exist yet. Handled
# as "could not check" rather than a failure -- see _classify.
REASON_MISSING = 'missing'

# eMMC PRE_EOL_INFO (EXT_CSD byte 267). 0x01 is normal; 0x02 means
# 80% of the reserved blocks are consumed and 0x03 means 90%.
PRE_EOL_LABELS = {0x01: 'normal', 0x02: 'warning', 0x03: 'urgent'}

# DEVICE_LIFE_TIME_EST (EXT_CSD 268/269) in 10% bands: 0x01 is 0-10%
# used, 0x0a is 90-100%, 0x0b means the estimate has been exceeded.
# Warn from 80% on, which leaves time to plan a swap rather than
# discover it during a support call.
WEAR_WARN_PCT = 80

# How far into a boot the watcher could plausibly have missed an
# error it never saw the count rise for: boot, container start,
# celery's migration wait, then the first sample. Generous, and it
# is the bound that keeps a skewed clock from dragging old errors
# into the current boot. See _assemble.
STARTUP_GRACE_S = 3600

# Card manufacturer IDs, transcribed from mmc-utils' lsmmc.c, which is
# the closest thing to a canonical table that exists. There is no
# public authoritative registry: JEDEC assigns eMMC MIDs and does not
# publish them freely, and the SD Association publishes nothing at
# all. Two entries here are literally "Unknown" because that is what
# upstream says -- kept verbatim rather than tidied away, so this
# stays a transcription rather than an interpretation.
#
# SD and eMMC are DIFFERENT namespaces and upstream keeps two separate
# tables for exactly that reason: 0x03 is SanDisk on SD but Toshiba on
# eMMC, and 0x02 is Toshiba/Kingston/Viking on SD but Kingston/SanDisk
# on eMMC. Sharing one table would produce a confident wrong answer,
# which is the failure mode this module avoids everywhere else.
#
# Source: https://git.kernel.org/pub/scm/utils/mmc/mmc-utils.git
# (lsmmc.c, sd_database / mmc_database). The kernel's own
# CID_MANFID_* defines in drivers/mmc/core/card.h agree where they
# overlap; its bus-untagged entries are deliberately not merged in
# here, because guessing which of the two namespaces they belong to
# is the mistake this split exists to prevent.
SD_MANUFACTURER_IDS = {
    0x01: 'Panasonic',
    0x02: 'Toshiba/Kingston/Viking',
    0x03: 'SanDisk',
    0x08: 'Silicon Power',
    0x18: 'Infineon',
    0x1B: 'Transcend/Samsung',
    0x1C: 'Transcend',
    0x1D: 'Corsair/AData',
    0x1E: 'Transcend',
    0x1F: 'Kingston',
    0x27: 'Delkin/Phison',
    0x28: 'Lexar',
    0x30: 'SanDisk',
    0x31: 'Silicon Power',
    0x33: 'STMicroelectronics',
    0x41: 'Kingston',
    0x6F: 'STMicroelectronics',
    0x74: 'Transcend',
    0x76: 'Patriot',
    0x82: 'Gobe/Sony',
    # Upstream carries 0x89 with no vendor; kept so the gap is visible.
    0x89: None,
    # From the kernel's CID_MANFID_KINGSTON_SD, which unlike its
    # neighbours is explicitly tagged as the SD-bus value.
    0x9F: 'Kingston',
}

EMMC_MANUFACTURER_IDS: dict[int, str | None] = {
    0x00: 'SanDisk',
    0x02: 'Kingston/SanDisk',
    0x03: 'Toshiba',
    0x05: None,
    0x06: None,
    0x11: 'Toshiba',
    0x13: 'Micron',
    0x15: 'Samsung/SanDisk/LG',
    0x2C: 'Kingston',
    0x37: 'KingMax',
    0x44: 'ATP',
    0x45: 'SanDisk Corporation',
    0x70: 'Kingston',
    0xFE: 'Micron',
}


_warned: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    """Log ``message`` at WARNING the first time, DEBUG thereafter.

    Deliberately a small local copy of the helper in
    :mod:`anthias_common.undervoltage` rather than a shared import:
    the two modules are siblings with no dependency between them, and
    the alternative was reaching into another module's private
    helper.

    The conditions this guards (an unreadable boot id) are properties
    of the device, not of an individual reading, so they are worth
    stating once. The watcher and every page render call in here, so
    without the throttle one persistent fault would bury the device
    log.
    """
    if key in _warned:
        logger.debug(message)
        return
    _warned.add(key)
    logger.warning(message)


def _read_text(path: str) -> str | None:
    """Stripped contents of a sysfs attribute, or ``None``.

    An empty attribute reads back as ``None`` rather than ``''``:
    ext4 leaves ``last_error_func`` empty on a filesystem that has
    never errored, and an empty string would reach the API as
    ``""`` where every other absent field is ``null``.
    """
    try:
        with open(path) as f:
            return f.read().strip() or None
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

    ``emergency_ro`` is the modern spelling and the reason this is a
    set rather than a single string. Newer kernels stop the filesystem
    by raising an internal emergency flag instead of setting
    ``SB_RDONLY``, so a filesystem that fails every write with
    ``EROFS`` reports mount options of ``rw,relatime`` and super
    options of ``rw,errors=remount-ro,emergency_ro``. Measured
    directly by injecting an error through ``trigger_fs_error`` on a
    loopback ext4: writes returned ``EROFS`` while both ``ro`` and
    ``statvfs``'s ``ST_RDONLY`` stayed clear, so this string was the
    only passive evidence available. Older kernels still use plain
    ``ro``; both spellings are live across the fleet.

    Even so, treat this as the fast path and not the proof. The write
    check in :func:`run_write_check` is what actually establishes
    whether the filesystem takes writes, precisely because the passive
    signals have already been caught missing this once.
    """
    for field in ('mount_options', 'super_options'):
        options = set(str(mount.get(field) or '').split(','))
        if options & READ_ONLY_OPTIONS:
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


def _merge_smart(
    info: dict[str, Any], disk: str, smart_fact: dict[str, Any] | None
) -> dict[str, Any]:
    """Fold the viewer's SMART fact into a SATA/NVMe device's info.

    The fact is ignored unless it names the same disk we resolved. A
    box with a boot SSD and a separate data drive would otherwise have
    one drive's wear reported against the other's, which is worse than
    reporting nothing: it is a confident wrong answer.
    """
    # Model name, which sysfs does give, so the card is identified
    # even when SMART is unavailable or the viewer is down.
    info['name'] = _read_text(
        os.path.join(SYS_BLOCK, disk, 'device', 'model')
    ) or _read_text(os.path.join(SYS_BLOCK, disk, 'device', 'name'))

    if not smart_fact or not smart_fact.get('supported'):
        return info

    reported = str(smart_fact.get('device') or '')
    if os.path.basename(reported) != disk:
        return info

    info['smart'] = smart_fact
    info['name'] = smart_fact.get('model') or info['name']
    info['wear_pct'] = smart_fact.get('wear_pct')
    info['wear_is_exact'] = bool(smart_fact.get('wear_is_exact'))
    info['wear_is_advisory'] = bool(smart_fact.get('wear_is_advisory'))
    info['pre_eol'] = smart_fact.get('pre_eol')
    return info


def read_media_info(
    disk: str | None, smart_fact: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Media kind, identity and wear for a whole device.

    ``smart_fact`` is the payload the viewer publishes (see
    ``anthias_common.smart``). It is only consulted for a SATA/NVMe
    device, where sysfs offers no wear signal at all; SD and eMMC have
    their own registers and never need it. Folding it in here rather
    than adding a parallel field is deliberate: an SSD and a compute
    module's soldered eMMC then render through the same
    ``wear_pct``/``pre_eol`` path, so the UI and the API need no third
    branch.
    """
    info: dict[str, Any] = {
        'kind': 'unknown',
        'name': None,
        'manufacturer': None,
        # Raw CID manufacturer id, kept alongside the resolved name so
        # an id with no published vendor is still reportable.
        'manufacturer_id': None,
        'manufactured': None,
        'wear_pct': None,
        # False when wear_pct is a band's upper bound (eMMC) or a
        # vendor ATA attribute; True only for NVMe's defined field.
        # Precision, which drives the "up to N%" copy.
        'wear_is_exact': False,
        # Trustworthiness, which is a different question and drives
        # the verdict: an eMMC band is imprecise but authoritative,
        # an ATA vendor attribute is neither.
        'wear_is_advisory': False,
        'pre_eol': None,
        # SMART-only detail, for the System Info disclosure. None on
        # every SBC booting from a card, which is most of the fleet.
        'smart': None,
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

    if info['kind'] == 'disk':
        # sysfs gives a SATA/NVMe device no wear or health signal at
        # all, so everything below has to come from SMART, which only
        # the privileged viewer can read.
        return _merge_smart(info, disk, smart_fact)

    if info['kind'] == 'unknown':
        return info

    info['name'] = _read_text(os.path.join(device_dir, 'name'))

    manfid = _read_int(os.path.join(device_dir, 'manfid'))
    if manfid is not None:
        table = (
            EMMC_MANUFACTURER_IDS
            if info['kind'] == 'emmc'
            else SD_MANUFACTURER_IDS
        )
        # Kept whether or not it resolves: a support engineer can look
        # up a raw id, and it is the only way an unlisted vendor is
        # reported at all.
        info['manufacturer_id'] = manfid
        # None, not "Unknown (0x88)". An id absent from the table is
        # one nobody has published a vendor for -- the Rock Pi 4
        # testbed's 0x88 is in neither mmc-utils nor the kernel nor
        # anything else citable -- and that string put a placeholder
        # where the UI expects a company name. The card still
        # identifies itself by product name, and the raw id stays in
        # the technical detail.
        info['manufacturer'] = table.get(manfid)

    # The MMC date register is MM/YYYY and has no day, so it stays a
    # string rather than being forced into a date the card never gave.
    info['manufactured'] = _read_text(os.path.join(device_dir, 'date'))

    # Always the upper bound of a 10% band, never a point reading:
    # DEVICE_LIFE_TIME_EST 0x01 means "0-10% used", so the Rock Pi 4
    # testbed's 0x01 becomes 10 here. Conservative in the right
    # direction for the warning threshold, but it is why the UI has to
    # say "up to 10%" rather than "about 10%" -- the true figure could
    # be zero.
    info['wear_pct'] = _parse_life_time(
        _read_text(os.path.join(device_dir, 'life_time'))
    )
    info['wear_is_exact'] = False
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
    # The data directory isn't there. That is a startup or
    # configuration state, not a storage fault, and it matters most on
    # Balena where the resin-data volume starts empty on a first boot.
    # Calling it a failure would put "this player can't save anything"
    # on a brand-new healthy device.
    if exc.errno in (errno.ENOENT, errno.ENOTDIR):
        return REASON_MISSING
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
            # Looped rather than a single os.write: a short write does
            # not raise, and near ENOSPC ext4 will happily accept part
            # of the buffer. The read-back would then differ from the
            # payload and be reported as REASON_CORRUPT -- "it is
            # handing back data that isn't what was written to it",
            # i.e. replace the card -- when the filesystem is merely
            # full.
            written = 0
            while written < len(payload):
                chunk = os.write(fd, payload[written:])
                if not chunk:
                    raise OSError(errno.ENOSPC, 'short write')
                written += chunk
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

    An unknown boot id discards the latch outright, matching the fix
    the under-voltage latch took for the same flaw: comparing ``None``
    to a stored ``None`` matches, so a device that could not read its
    boot id would treat last week's latch as current and never reset.
    Degrading to "live readings only" can under-report history but
    never invent it, and a stuck ``write_failed_since_boot`` would
    warn about a card that had already been replaced.
    """
    if boot_id is None:
        return _blank_latch()

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
    """Persist the latch, unless doing so would be pointless or unsafe.

    Two gates, both borrowed from the under-voltage latch after it hit
    the same problems.

    Without a boot id there is nothing to key the reset on, so a latch
    written now could outlive the boot it describes and warn forever.
    Skipping the write keeps this consistent with :func:`_load_latch`,
    which discards such a latch anyway.

    And a write that would change nothing is skipped, because Redis
    persists to the card: the watcher samples every
    ``SAMPLE_INTERVAL_S``, so an unconditional SET is ~1,400
    appendonly-fsynced writes a day onto the storage of a device that
    is behaving perfectly. Writing to a card that often in order to
    check whether the card is wearing out would be self-defeating.
    """
    if boot_id is None:
        _warn_once(
            'no_boot_id',
            'No kernel boot id available; reporting storage health from '
            'live readings only and not persisting history.',
        )
        return

    payload = dict(latch)
    payload['boot_id'] = boot_id
    serialized = json.dumps(payload, sort_keys=True)

    try:
        existing = redis_client.get(REDIS_KEY)
        if isinstance(existing, bytes):
            existing = existing.decode('utf-8')
        if existing == serialized:
            return
    except Exception:
        # Fall through and attempt the write anyway; a read that
        # failed says nothing about whether the write will. Not logged
        # here because the write below reports its own failure, and a
        # broken Redis would otherwise produce two lines per sample.
        logger.debug(
            'Could not read the storage-health latch back; writing '
            'unconditionally.',
            exc_info=True,
        )

    try:
        redis_client.set(REDIS_KEY, serialized)
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


def probe(
    data_dir: str | None = None,
    smart_fact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything readable without writing anything or touching Redis.

    ``smart_fact`` is passed in rather than fetched so this stays
    free of Redis: the callers that have a client
    (:func:`get_state`, :func:`record_check`) read it and hand it
    down, and the watcher's startup probe can skip it entirely.
    """
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
        'media': read_media_info(resolved['disk'], smart_fact),
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

    media = state['media']
    if media['pre_eol'] in ('warning', 'urgent'):
        return STATUS_WEAR
    # An advisory wear figure is displayed but never raises the
    # warning by itself. ATA has no defined wear field, only vendor
    # attributes that count down from 100 by convention, and a drive
    # inverting that convention would read as nearly worn out when
    # new. smart.py's docstring already said the well-defined signals
    # carry the verdict; this is where that becomes true. eMMC bands
    # and NVMe percentage_used are both authoritative and still count.
    wear = media['wear_pct']
    if (
        wear is not None
        and not media.get('wear_is_advisory')
        and wear >= WEAR_WARN_PCT
    ):
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
    errors_new = (
        max(0, errors['count'] - baseline) if baseline is not None else 0
    )

    # This signal exists for one narrow case: an error that landed
    # during this boot but *before* the watcher took its baseline --
    # a mount-time error on a bad card. Outside that gap, a rising
    # count is what reports an error, and errors_new already has it.
    #
    # So the window is bounded by the gap it is meant to cover. Left
    # unbounded it was a clock-skew trap: boot_time is derived as
    # ``time.time() - uptime``, and a Pi has no RTC, so a clock
    # restored behind real time by fake-hwclock pushes boot_time into
    # the past and drags errors from *previous* boots inside it. On a
    # device up for a month that turned an amber "recorded errors"
    # into a red "returning errors since it last restarted" with no
    # new error at all. An error dated an hour into a month-long
    # uptime cannot be one the watcher missed at startup.
    startup_gap = (
        boot_time is not None
        and last_epoch
        and boot_time - 60 <= last_epoch <= boot_time + STARTUP_GRACE_S
    )
    errors_this_boot = bool(
        errors['supported'] and (errors_new > 0 or startup_gap)
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
        'errors_new': errors_new,
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


def _fold_write_check(latch: dict[str, Any], result: dict[str, Any]) -> None:
    """Merge one write-check result into the latch."""
    missing = result['reason'] == REASON_MISSING

    # None, not False, when the directory simply is not there:
    # ``_classify_status`` branches on ``write_ok is False``, so this
    # keeps a not-yet-created data directory out of the failure path
    # entirely rather than relying on every consumer to special-case
    # the reason.
    latch['write_ok'] = None if missing else result['ok']
    latch['write_reason'] = result['reason']
    latch['last_check'] = result['checked_at']
    if result['fsync_ms'] is not None:
        latch['fsync_ms'] = result['fsync_ms']

    # ENOSPC is excluded from the latch on purpose. The latch's
    # rationale -- a write that failed and later succeeded is not a
    # card that is fine -- holds for EIO and EROFS but not for a full
    # filesystem, which is not a hardware fault at all and is
    # separated everywhere else in this module. Latching it turned the
    # banner from "run out of space" into "replace the memory card"
    # the moment the operator did what it asked and deleted some
    # assets, and left it there until the next reboot.
    transient = missing or result['reason'] == REASON_NO_SPACE
    if result['ok'] or transient:
        return

    if not latch['write_failed_since_boot']:
        latch['write_failed_since_boot'] = True
        latch['first_write_fail'] = result['checked_at']
    latch['last_write_fail'] = result['checked_at']
    latch['write_fail_count'] = (latch['write_fail_count'] or 0) + 1


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

    facts = probe(data_dir, smart.read(redis_client))
    latch = _load_latch(redis_client, boot_id)

    if facts['errors']['supported'] and latch['errors_baseline'] is None:
        latch['errors_baseline'] = facts['errors']['count']

    if write_check and facts['supported']:
        _fold_write_check(latch, run_write_check(data_dir))

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
    facts = probe(data_dir, smart.read(redis_client))
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
