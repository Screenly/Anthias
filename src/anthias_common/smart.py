"""SMART health for the SSD/NVMe on x86 and the arm64 boards.

:mod:`anthias_common.storage_health` covers the memory card an SBC
boots from. It cannot cover an SSD, because everything it reads is
either filesystem-level (ext4's counters) or an MMC register that a
SATA/NVMe device does not have. On x86 the wear signal lives behind
SMART, and SMART is not in sysfs: reading it means an ``SG_IO`` ioctl
on a SATA device or ``NVME_IOCTL_ADMIN_CMD`` on an NVMe one.

Which is why this module is split producer/consumer.

**Only the viewer can collect it.** Those ioctls need the device node
and ``CAP_SYS_RAWIO``/``CAP_SYS_ADMIN``. anthias-server and
anthias-celery are deliberately unprivileged and are handed no
devices, and on Balena they cannot be handed any: the compose file is
baked into the release from a workstation, nothing on-device can
enumerate the host, and a statically listed node that turns out to be
absent stops the container from starting. anthias-viewer is
``privileged: true`` in all three compose templates including both
Balena ones, so it sees the host's ``/dev`` and can run ``smartctl``.

This is the same problem HDMI-CEC had and it gets the same answer --
see ``anthias_server/lib/cec_client.py`` for the full reasoning. The
difference is the transport: CEC needs request-reply because a power
command is synchronous, whereas SMART changes over hours. So the
viewer samples on a slow cadence and publishes a Redis fact, and the
server reads it directly, the way ``cec:available`` and the display
resolution already work. The key carries a TTL so a dead viewer makes
the data expire rather than leaving the UI showing a stale verdict
forever.

A note on the numbers. NVMe reports wear unambiguously:
``percentage_used`` is a defined field meaning percent of rated life
consumed. ATA has no such field, only vendor attributes whose
*normalized* value conventionally counts down from 100 as life is
consumed. That convention is near-universal but it is a convention,
not a spec, and a few drives invert it. So NVMe is trusted outright,
ATA wear is treated as advisory, and the failure signals that are
actually well-defined -- the overall self-assessment, NVMe's spare
threshold, and reallocated/pending sector counts -- carry the
verdict.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Absolute path, because it is also what the sudoers rule authorises and
# the two must agree exactly for the rule to match.
SMARTCTL = '/usr/sbin/smartctl'

# Redis key holding the fact the viewer publishes. ``host:`` prefix
# matches the convention for host-shape facts.
REDIS_KEY = 'host:smart'

# Liveness, borrowed from the display-resolution reporter: longer than
# the sample interval so a slow sample never blanks the card, short
# enough that a viewer which died hours ago stops being quoted as
# current.
TTL_S = 3 * 3600

# smartctl is talking to hardware; on a drive that is already
# struggling an ATA command can hang. Bounded so the viewer's sampler
# thread cannot wedge.
TIMEOUT_S = 30

# Reallocated and pending sectors: the drive found a bad block and
# either remapped it or is about to. Well-defined across vendors,
# unlike the wear attributes.
ATA_REALLOCATED_SECTOR_CT = 5
ATA_POWER_ON_HOURS = 9
ATA_REALLOCATED_EVENT_CT = 196
ATA_CURRENT_PENDING_SECTOR = 197
ATA_OFFLINE_UNCORRECTABLE = 198

# Vendor wear attributes, best first. Each normalizes to "percent of
# life REMAINING" counting down from 100, so wear is 100 - value. See
# the module docstring on why this is advisory.
# NVMe CRITICAL_WARNING bits. Bit 1 is a temperature excursion,
# which is a cooling problem and not an end-of-life signal: treating
# the whole byte as critical put "your storage is wearing out, plan a
# replacement" on any drive that had once run hot. The rest genuinely
# do mean the drive is going.
NVME_CRIT_SPARE_LOW = 1 << 0
NVME_CRIT_TEMPERATURE = 1 << 1
NVME_CRIT_DEGRADED = 1 << 2
NVME_CRIT_READ_ONLY = 1 << 3
NVME_CRIT_VOLATILE_BACKUP_FAILED = 1 << 4
NVME_CRIT_END_OF_LIFE = (
    NVME_CRIT_SPARE_LOW
    | NVME_CRIT_DEGRADED
    | NVME_CRIT_READ_ONLY
    | NVME_CRIT_VOLATILE_BACKUP_FAILED
)

ATA_WEAR_ATTRS = (
    231,  # SSD_Life_Left
    202,  # Percent_Lifetime_Remain
    233,  # Media_Wearout_Indicator
    177,  # Wear_Leveling_Count
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


def _blank(device: str | None = None) -> dict[str, Any]:
    return {
        'supported': False,
        'device': device,
        'model': None,
        'firmware': None,
        'passed': None,
        'wear_pct': None,
        'wear_is_exact': False,
        # True when wear came from an ATA vendor attribute. Those
        # count down from 100 by convention rather than by spec, and a
        # drive that inverts the convention reads as nearly worn out
        # when new -- so the figure is shown but is not allowed to
        # raise a warning on its own. See the module docstring.
        'wear_is_advisory': False,
        'power_on_hours': None,
        'reallocated_sectors': None,
        'pending_sectors': None,
        'media_errors': None,
        'temperature_c': None,
        'pre_eol': None,
        'checked_at': _now_iso(),
    }


def _argv(device: str) -> list[str]:
    """The smartctl command line, elevated when we are not root.

    ``privileged: true`` on the viewer container is not enough on its
    own: ``bin/start_viewer.sh`` drops to the unprivileged ``viewer``
    user, so by the time this runs the process has neither root nor
    ``CAP_SYS_RAWIO``, and ``/dev/sda`` is ``root:disk`` besides.
    Measured on the x86 testbed -- as ``viewer``, smartctl reports
    "open device: /dev/sda failed: Permission denied"; as root the
    same call succeeds.

    ``sudo -n`` never prompts, so on an image without the rule this
    fails fast and is reported as unsupported rather than hanging a
    sampler thread on a password prompt. The root branch keeps a
    direct call working for tests, a debug shell, and anywhere the
    caller already has privilege.
    """
    args = [SMARTCTL, '--json', '-H', '-A', '-i', device]
    if os.geteuid() == 0:
        return args
    return ['sudo', '-n', *args]


def run_smartctl(device: str) -> dict[str, Any] | None:
    """Raw ``smartctl --json`` output for ``device``, or ``None``.

    Returns the parsed document even when smartctl reports a failure,
    because "this device does not support SMART" is itself an answer
    the caller needs; ``None`` is reserved for smartctl being absent
    or unrunnable.
    """
    try:
        proc = subprocess.run(
            _argv(device),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        # smartmontools is only installed on the boards that can have
        # a SMART-capable device. Absent is normal, not an error.
        return None
    except (subprocess.SubprocessError, OSError):
        logger.warning('smartctl failed on %s', device, exc_info=True)
        return None

    try:
        doc = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    return doc if isinstance(doc, dict) else None


def _ata_attrs(doc: dict[str, Any]) -> dict[int, dict[str, Any]]:
    table = (doc.get('ata_smart_attributes') or {}).get('table') or []
    out: dict[int, dict[str, Any]] = {}
    for row in table:
        if isinstance(row, dict) and isinstance(row.get('id'), int):
            out[row['id']] = row
    return out


def _raw(attrs: dict[int, dict[str, Any]], attr_id: int) -> int | None:
    row = attrs.get(attr_id)
    if not row:
        return None
    value = (row.get('raw') or {}).get('value')
    return value if isinstance(value, int) else None


def _ata_wear(attrs: dict[int, dict[str, Any]]) -> int | None:
    for attr_id in ATA_WEAR_ATTRS:
        row = attrs.get(attr_id)
        if not row:
            continue
        remaining = row.get('value')
        if isinstance(remaining, int) and 0 <= remaining <= 100:
            return 100 - remaining
    return None


def _parse_nvme(state: dict[str, Any], nvme: dict[str, Any]) -> None:
    """Fold the NVMe health log into ``state``.

    NVMe defines every field it reports, so these are read straight
    through with no interpretation.
    """
    used = nvme.get('percentage_used')
    if isinstance(used, int):
        state['wear_pct'] = min(used, 100)
        state['wear_is_exact'] = True

    for field in ('power_on_hours', 'media_errors'):
        value = nvme.get(field)
        if isinstance(value, int):
            state[field] = value

    spare = nvme.get('available_spare')
    threshold = nvme.get('available_spare_threshold')
    if (
        isinstance(spare, int)
        and isinstance(threshold, int)
        and spare < threshold
    ):
        # The drive has burned through the spare blocks it keeps to
        # remap failures. This is the NVMe spelling of eMMC's PRE_EOL
        # urgent.
        state['pre_eol'] = 'urgent'

    critical = nvme.get('critical_warning')
    if isinstance(critical, int) and critical & NVME_CRIT_END_OF_LIFE:
        state['pre_eol'] = 'urgent'


def _parse_ata(state: dict[str, Any], doc: dict[str, Any]) -> None:
    """Fold the ATA attribute table into ``state``.

    Unlike NVMe, none of this is specified: the wear figure comes from
    a vendor attribute and is flagged advisory so it cannot raise a
    warning on its own. See the module docstring.
    """
    attrs = _ata_attrs(doc)
    state['wear_pct'] = _ata_wear(attrs)
    state['wear_is_advisory'] = state['wear_pct'] is not None
    state['power_on_hours'] = _raw(attrs, ATA_POWER_ON_HOURS)
    state['reallocated_sectors'] = _raw(attrs, ATA_REALLOCATED_SECTOR_CT)
    state['pending_sectors'] = _raw(attrs, ATA_CURRENT_PENDING_SECTOR)


def parse(doc: dict[str, Any], device: str) -> dict[str, Any]:
    """Normalize a smartctl document into the shape the UI consumes.

    Deliberately produces the same ``wear_pct`` / ``pre_eol``
    vocabulary that ``storage_health`` already uses for eMMC, so an
    SSD and a compute module's soldered storage render through one
    code path instead of two.
    """
    state = _blank(device)

    # No SMART here: a virtio disk, a USB bridge smartctl can't see
    # through, or an SD card. Not an error, just nothing to report.
    if not doc or 'smart_status' not in doc:
        return state

    state['supported'] = True
    state['model'] = doc.get('model_name')
    state['firmware'] = doc.get('firmware_version')

    passed = (doc.get('smart_status') or {}).get('passed')
    state['passed'] = passed if isinstance(passed, bool) else None

    temperature = (doc.get('temperature') or {}).get('current')
    if isinstance(temperature, int):
        state['temperature_c'] = temperature

    nvme = doc.get('nvme_smart_health_information_log') or {}
    if nvme:
        _parse_nvme(state, nvme)
    else:
        _parse_ata(state, doc)

    if state['passed'] is False:
        # The drive's own self-assessment says it expects to fail.
        state['pre_eol'] = 'urgent'
    elif state['pre_eol'] is None:
        remapped = (state['reallocated_sectors'] or 0) + (
            state['pending_sectors'] or 0
        )
        if remapped or (state['media_errors'] or 0):
            state['pre_eol'] = 'warning'

    return state


def collect(device: str) -> dict[str, Any]:
    """Sample SMART for one device. Viewer-side; needs privilege."""
    doc = run_smartctl(device)
    if doc is None:
        return _blank(device)
    return parse(doc, device)


def publish(redis_client: Any, state: dict[str, Any]) -> None:
    """Store the fact for anthias-server to read.

    TTL'd rather than written once: the key expiring is how the
    server learns the viewer stopped reporting, so a card that has
    been dead for a day is not quoted as current.
    """
    try:
        redis_client.set(REDIS_KEY, json.dumps(state), ex=TTL_S)
    except Exception:
        logger.warning('Could not publish the SMART fact', exc_info=True)


def read(redis_client: Any) -> dict[str, Any] | None:
    """The published fact, or ``None`` if absent, stale or unreadable.

    ``None`` and an unsupported-but-present fact are different
    answers: the first means nobody has reported, the second means the
    viewer looked and the device has no SMART. Callers that render a
    verdict need to keep them apart.
    """
    try:
        raw = redis_client.get(REDIS_KEY)
    except Exception:
        return None
    if not raw:
        return None
    if isinstance(raw, bytes):
        try:
            raw = raw.decode('utf-8')
        except UnicodeDecodeError:
            return None
    try:
        state = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return state if isinstance(state, dict) else None
