"""Server-side client for CEC, which is executed by the viewer.

Why the viewer owns the hardware
--------------------------------
``/dev/cec*`` is reachable from the **viewer** container on every board
and every deployment, because it is ``privileged: true`` in all three
compose templates — including both balena ones. anthias-server and
anthias-celery are not privileged and are handed no CEC devices.

Passing the nodes into those two containers works on the plain
docker-compose install (an on-device step can enumerate the host's
``/dev/cec*`` before ``up``), but **cannot** be made to work on balena:
the compose file is baked into the release from a workstation, nothing
on-device can enumerate the host, and a statically listed node that
turns out to be absent stops the container from starting at all. Since
balena support is a requirement, the hardware access lives in the one
container that already has it everywhere.

Measured consequences, all to the good:

  * **No device passthrough anywhere.** The compose templates need no
    ``devices:`` entries for server/celery, so there is no OTA upgrade
    risk and no board-specific casing.
  * **The Pi 2 works.** Verified on the testbed: server and celery see
    no CEC node at all, while the viewer sees ``/dev/cec0`` with a live
    physical address (1.0.0.0) and transmits in 465 ms. That board could
    never have worked through the server.
  * server and celery stay unprivileged and gain no device access.

The cost is that CEC depends on the viewer being up. That is already
true of the local blank/unblank fallback, and a wedged viewer is a
much louder problem than display power.

Transport is the existing request-reply bus: publish
``<command>&<correlation-id>`` on the ``anthias.viewer`` channel and
BLPOP the reply — the same mechanism the v1 ``current_asset_id``
endpoint uses.
"""

import logging
import uuid
from typing import Any

from anthias_common.errors import ReplyTimeoutError
from anthias_common.utils import connect_to_redis
from anthias_server.lib.cec import PowerStatus
from anthias_server.settings import ReplyCollector, ViewerPublisher

logger = logging.getLogger(__name__)

#: Redis key the viewer writes at startup describing whether this device
#: has any CEC adapter. Read directly rather than round-tripped, because
#: ``available()`` gates a settings-page render and must stay cheap.
CEC_AVAILABLE_KEY = 'cec:available'

#: Reply budget for a power *query*. The slowest measured fan-out is
#: 3.2 s (Pi 2, Cortex-A7, one live adapter), and the viewer's own
#: per-adapter guard is 5 s, so allow room for two adapters plus the bus
#: hop without waiting anywhere near the celery soft limit.
QUERY_TIMEOUT_MS = 12000

#: Reply budget for a power *command*. Transmits are quicker than
#: queries (no reply to wait for on the CEC bus itself): 431-465 ms
#: measured across the fleet.
COMMAND_TIMEOUT_MS = 8000


class ViewerUnavailableError(Exception):
    """The viewer did not answer, so nothing was done.

    Deliberately distinct from "the display did not answer": callers
    that latch state must retry rather than record a command as
    delivered.
    """


def available() -> bool:
    """Whether this device has any CEC adapter at all.

    Reads the fact the viewer publishes at startup instead of asking it,
    because this gates the settings-page render. A missing key means
    "the viewer has not reported yet" and is treated as unavailable —
    the controls appear once it has.
    """
    try:
        raw = connect_to_redis().get(CEC_AVAILABLE_KEY)
    except Exception as exc:
        logger.warning('Could not read CEC availability: %s', exc)
        return False
    if raw is None:
        return False
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8', errors='replace')
    return str(raw) == '1'


def _ask(command: str, timeout_ms: int) -> dict[str, Any]:
    """Send a request-reply command to the viewer and return its reply."""
    correlation_id = uuid.uuid4().hex
    try:
        ViewerPublisher.get_instance().send_to_viewer(
            f'{command}&{correlation_id}'
        )
        reply = ReplyCollector.get_instance().recv_json(
            correlation_id, timeout_ms
        )
    except ReplyTimeoutError as exc:
        raise ViewerUnavailableError(
            f'viewer did not answer {command} within {timeout_ms}ms'
        ) from exc
    if not isinstance(reply, dict):
        raise ViewerUnavailableError(
            f'malformed reply to {command}: {reply!r}'
        )
    return reply


def power_status() -> PowerStatus:
    """Ask the viewer for the aggregated display power state."""
    reply = _ask('display_power_status', QUERY_TIMEOUT_MS)
    raw = reply.get('status')
    try:
        return PowerStatus(str(raw))
    except ValueError:
        logger.warning('Viewer reported unknown CEC status %r', raw)
        return PowerStatus.ERROR


def set_power(on: bool) -> tuple[int, int]:
    """Ask the viewer to drive every live adapter on or off.

    Returns ``(acknowledged, attempted)``, matching the device-layer
    contract so callers do not care where the work happened.
    """
    command = 'display_on' if on else 'display_off'
    reply = _ask(command, COMMAND_TIMEOUT_MS)
    if reply.get('busy'):
        # The viewer had another CEC operation in flight and skipped
        # this one. Nothing was transmitted.
        raise ViewerUnavailableError('viewer CEC bus was busy')
    return int(reply.get('acknowledged', 0)), int(reply.get('attempted', 0))


def publish_availability(value: bool) -> None:
    """Record whether this device has a CEC adapter. Called by the viewer.

    No TTL: it is a hardware fact for the life of the container, and the
    viewer rewrites it on every start.
    """
    connect_to_redis().set(CEC_AVAILABLE_KEY, '1' if value else '0')
