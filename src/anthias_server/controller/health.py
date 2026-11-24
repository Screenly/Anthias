"""Health of managed screens, from the ``/api/v2/info`` the controller polls.

Screens push their ``/api/v2/info`` health over their connection to
the controller (``push``); the status poll (``service.check_node``)
fetches it from screens that can't. This module keeps the parts an operator
acts on: free disk space, storage health, under-voltage, available
memory, load and uptime. It turns them into short warnings for the
Screens page, and into anonymous counts for the daily analytics event
(see :func:`cluster_summary` and ``lib.telemetry``).

Nothing here makes a request. It reads only what the poll already
stored, so a screen that has gone quiet keeps its last reading but is
not counted as healthy or unhealthy once the reading is stale.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import Node

# Below this much free space a publish can fail halfway through a
# video upload, which is the failure an operator can still prevent.
LOW_DISK_BYTES = 1024**3
# The viewer starts dropping frames and the kernel starts killing
# processes on a 1 GB board somewhere below this.
LOW_MEMORY_MIB = 64
# Storage-health verdicts (anthias_common.storage_health) that need
# attention, with the wording the Screens page uses.
STORAGE_PROBLEMS = {
    'wear': 'SD card wearing out',
    'errors': 'Storage errors',
    'full': 'Storage full',
    'failing': 'Storage failing',
}
# A reading is renewed at least every 15 minutes: a pushing screen
# sends its health that often even when nothing changed
# (push.HEALTH_PUSH_MAX_S, checked on its one-minute heartbeat), and a
# screen that can't push is polled once its last check is 15 minutes
# old (tasks.FALLBACK_POLL_S), by a run every five minutes that may
# itself take a few. Older than this, the reading belongs to a screen
# that has stopped answering; any shorter and a healthy screen would
# flicker to "unknown" between two readings.
HEALTH_MAX_AGE = timedelta(minutes=25)

_SIZE_RE = re.compile(r'^([0-9]+(?:[.,][0-9]+)?)\s*([a-z]+)$')
_UNITS = {
    'byte': 1,
    'bytes': 1,
    'kb': 1024,
    'mb': 1024**2,
    'gb': 1024**3,
    'tb': 1024**4,
    'pb': 1024**5,
}


def parse_size(text: Any) -> int | None:
    """Bytes from Django's ``filesizeformat`` output (``17.9 GB``).

    ``/api/v2/info`` only reports free space in that human form, and
    changing the field would change the v2 wire shape. ``None`` when
    the text is not a size.
    """
    if not isinstance(text, str):
        return None
    normalized = ' '.join(text.replace('\xa0', ' ').split()).lower()
    match = _SIZE_RE.match(normalized)
    if not match or match.group(2) not in _UNITS:
        return None
    value = float(match.group(1).replace(',', '.'))
    return int(value * _UNITS[match.group(2)])


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return int(value)


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return round(float(value), 2)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def health_from_info(info: dict[str, Any]) -> dict[str, Any]:
    """The subset of ``/api/v2/info`` worth keeping, type-checked.

    Screens on an older Anthias omit some sections; every field then
    falls back to "unknown" rather than failing the status check.
    """
    memory = _dict(info.get('memory'))
    under_voltage = _dict(info.get('under_voltage'))
    storage = _dict(info.get('storage'))
    uptime = _dict(info.get('uptime'))
    days, hours = _float(uptime.get('days')), _float(uptime.get('hours'))
    uptime_hours = (
        round(days * 24 + hours, 1)
        if days is not None and hours is not None
        else None
    )
    free_space = info.get('free_space')
    return {
        'free_space': str(free_space or '').replace('\xa0', ' ')[:32],
        'free_space_bytes': parse_size(free_space),
        'memory_total_mib': _int(memory.get('total')),
        'memory_available_mib': _int(memory.get('available')),
        'loadavg': _float(info.get('loadavg')),
        'uptime_hours': uptime_hours,
        'under_voltage_supported': bool(under_voltage.get('supported')),
        'under_voltage_active': bool(under_voltage.get('active')),
        'under_voltage_seen': bool(under_voltage.get('seen_since_boot')),
        'storage_status': str(storage.get('status') or 'unknown')[:16],
        'display_power': str(info.get('display_power') or '')[:64],
    }


def problems(health: dict[str, Any]) -> list[tuple[str, str]]:
    """``(key, label)`` for every health warning in one reading."""
    found: list[tuple[str, str]] = []
    free = health.get('free_space_bytes')
    if isinstance(free, int) and free < LOW_DISK_BYTES:
        found.append(
            ('low_disk', f'Low disk space: {health.get("free_space")} free')
        )
    storage = health.get('storage_status')
    if storage in STORAGE_PROBLEMS:
        found.append(('storage', STORAGE_PROBLEMS[storage]))
    if health.get('under_voltage_active'):
        found.append(('under_voltage', 'Under-voltage now'))
    elif health.get('under_voltage_seen'):
        found.append(('under_voltage', 'Under-voltage since boot'))
    available = health.get('memory_available_mib')
    if isinstance(available, int) and available < LOW_MEMORY_MIB:
        found.append(('low_memory', f'Low memory: {_mib(available)} free'))
    return found


def _mib(value: int) -> str:
    """Memory in the same units as disk space (``filesizeformat``)."""
    from django.template.defaultfilters import filesizeformat

    return str(filesizeformat(value * 1024 * 1024))


def _uptime(hours: Any) -> str:
    if not isinstance(hours, (int, float)):
        return ''
    if hours < 1:
        return 'Up <1 h'
    if hours < 48:
        return f'Up {round(hours)} h'
    return f'Up {round(hours / 24)} days'


def summary_line(health: dict[str, Any]) -> str:
    """One line for the Screens page: disk, memory, uptime."""
    return ' · '.join(summary_parts(health))


def summary_parts(health: dict[str, Any]) -> list[str]:
    """Disk, memory and uptime, each a separate item."""
    parts = []
    if health.get('free_space'):
        parts.append(f'{health["free_space"]} disk free')
    if isinstance(health.get('memory_available_mib'), int):
        parts.append(f'{_mib(health["memory_available_mib"])} RAM free')
    uptime = _uptime(health.get('uptime_hours'))
    if uptime:
        parts.append(uptime)
    return parts


def is_fresh(node: Node, now: datetime) -> bool:
    """Whether ``node``'s stored reading describes it right now."""
    from .models import NodeStatus

    return (
        node.status == NodeStatus.ONLINE
        and bool(node.health)
        and node.health_at is not None
        and now - node.health_at <= HEALTH_MAX_AGE
    )


def cluster_summary(nodes: Iterable[Node], now: datetime) -> dict[str, int]:
    """Anonymous counts describing the whole set of managed screens.

    Only counts: no names, addresses, fingerprints or versions, so it
    is safe to send with the daily analytics event. Health counts cover
    screens with a fresh reading (``screens_with_health``); a screen
    that is offline is counted by its status instead.
    """
    from .models import NodeStatus, PublishState

    counts = {
        'screens_total': 0,
        'screens_online': 0,
        'screens_offline': 0,
        'screens_left': 0,
        'screens_cert_changed': 0,
        'screens_unknown': 0,
        'screens_publish_ok': 0,
        'screens_publish_failed': 0,
        'screens_with_health': 0,
        'screens_low_disk': 0,
        'screens_storage_problem': 0,
        'screens_under_voltage': 0,
        'screens_low_memory': 0,
    }
    by_status: dict[str, str] = {
        NodeStatus.ONLINE.value: 'screens_online',
        NodeStatus.OFFLINE.value: 'screens_offline',
        NodeStatus.UNAUTHORIZED.value: 'screens_left',
        NodeStatus.WRONG_CERT.value: 'screens_cert_changed',
        NodeStatus.UNKNOWN.value: 'screens_unknown',
    }
    by_problem = {
        'low_disk': 'screens_low_disk',
        'storage': 'screens_storage_problem',
        'under_voltage': 'screens_under_voltage',
        'low_memory': 'screens_low_memory',
    }
    for node in nodes:
        counts['screens_total'] += 1
        counts[by_status.get(node.status, 'screens_unknown')] += 1
        if node.publish_state == PublishState.OK:
            counts['screens_publish_ok'] += 1
        elif node.publish_state == PublishState.FAILED:
            counts['screens_publish_failed'] += 1
        if not is_fresh(node, now):
            continue
        counts['screens_with_health'] += 1
        for key in {key for key, _ in problems(node.health)}:
            counts[by_problem[key]] += 1
    return counts
