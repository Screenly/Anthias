"""In-container reader for host system logs.

One platform-agnostic entry point — ``get_journal_snapshot`` — with
two backends behind it. The caller does not need to know which
platform it is on; the returned snapshot names the backend that
answered so the API can report it.

Snapshot-only by design. Neither backend offers ``follow``: the
bare-metal host agent's ``subscriber_loop`` is single-threaded and a
streaming read would wedge reboot/shutdown handling behind it, and
offering a live tail on Balena alone would mean two divergent
contracts and a capability matrix in the UI. See
``anthias_common.journal`` for the shared caps and wire contract.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import redis
import requests

from anthias_common import journal
from anthias_common.utils import (
    connect_to_redis,
    get_balena_supervisor_api_response,
    is_balena_app,
)

logger = logging.getLogger(__name__)

#: Connect / read budget for the supervisor call. The read side is
#: generous because journalctl on a busy host can take a moment to
#: produce 2000 lines, but it is bounded so a wedged supervisor can't
#: pin a request worker.
_SUPERVISOR_TIMEOUT = (5, 30)

SOURCE_BALENA = 'balena'
SOURCE_HOST_AGENT = 'host_agent'


class JournalUnavailable(Exception):
    """The platform's log backend could not answer this request.

    Distinct from ``journal.JournalRequestError`` (a bad request, 400):
    this is "we asked correctly and the backend didn't deliver", which
    the API surfaces as a 502/503.
    """


@dataclass(frozen=True)
class JournalSnapshot:
    """One journal read, normalised across both backends."""

    entries: list[dict[str, Any]]
    #: Which backend answered — ``balena`` or ``host_agent``.
    source: str
    #: True when the payload hit the byte cap and the oldest entries
    #: in the requested window were dropped.
    truncated: bool
    #: Whether the host keeps its journal across reboots. ``None``
    #: when the backend can't tell us (Balena answers through the
    #: supervisor and never exposes the host's journald storage mode,
    #: but balenaOS persists it).
    persistent: bool | None


def get_journal_snapshot(
    *,
    count: int = journal.DEFAULT_COUNT,
    unit: str | None = None,
    container: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> JournalSnapshot:
    """Read the host journal on whichever platform we're running on.

    Raises ``journal.JournalRequestError`` for a filter we refuse to
    forward, and ``JournalUnavailable`` when the backend is reachable
    but didn't produce a snapshot.
    """
    journal.validate_filters(
        unit=unit, container=container, since=since, until=until
    )
    count = journal.clamp_count(count)

    if is_balena_app():
        return _snapshot_via_supervisor(
            count=count,
            unit=unit,
            container=container,
            since=since,
            until=until,
        )
    return _snapshot_via_host_agent(
        count=count,
        unit=unit,
        container=container,
        since=since,
        until=until,
    )


def _snapshot_via_supervisor(
    *,
    count: int,
    unit: str | None,
    container: str | None,
    since: str | None,
    until: str | None,
) -> JournalSnapshot:
    """Balena backend: supervisor ``POST /v2/journal-logs``.

    Needs no compose change and no new privilege —
    ``anthias-server`` already carries
    ``io.balena.features.supervisor-api``, which is the only thing
    this endpoint requires.

    ``format: 'json'`` (supervisor >= 10.3) is what makes the payload
    identical in shape to the bare-metal ``journalctl -o json``
    output, so one normaliser serves both. ``since`` / ``until`` need
    supervisor >= 14.7; an older supervisor ignores them rather than
    erroring, which would silently widen the window — so the API
    reports the backend that answered and callers that care can check
    the returned timestamps.
    """
    body: dict[str, Any] = {
        'all': True,
        'follow': False,
        'format': 'json',
        'count': count,
    }
    if unit:
        body['unit'] = unit
    if container:
        body['containerId'] = container
    if since:
        body['since'] = since
    if until:
        body['until'] = until

    try:
        response = get_balena_supervisor_api_response(
            method='post',
            action='journal-logs',
            version='v2',
            json=body,
            stream=True,
            timeout=_SUPERVISOR_TIMEOUT,
        )
    except requests.RequestException as error:
        raise JournalUnavailable(
            f'Could not reach the Balena supervisor: {error}'
        ) from error

    try:
        if not response.ok:
            raise JournalUnavailable(
                'Balena supervisor returned '
                f'HTTP {response.status_code} for journal-logs.'
            )
        payload, truncated = _read_capped(response)
    finally:
        response.close()

    return JournalSnapshot(
        entries=journal.normalize_payload(payload),
        source=SOURCE_BALENA,
        truncated=truncated,
        # balenaOS persists the journal; the supervisor gives us no
        # way to confirm the host's storage mode, so this is asserted
        # from the platform rather than measured.
        persistent=True,
    )


def _read_capped(response: requests.Response) -> tuple[str, bool]:
    """Drain a streamed supervisor response up to the byte cap.

    Streamed rather than buffered because the supervisor hands back
    whatever journalctl produces, and ``count`` bounds lines, not
    bytes — a handful of multi-megabyte messages would otherwise be
    read into memory in full before we got to cap them.
    """
    chunks: list[bytes] = []
    total = 0
    truncated = False
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
        if total >= journal.MAX_PAYLOAD_BYTES:
            truncated = True
            break
    raw = b''.join(chunks)[: journal.MAX_PAYLOAD_BYTES]
    return raw.decode('utf-8', 'replace'), truncated


def _snapshot_via_host_agent(
    *,
    count: int,
    unit: str | None,
    container: str | None,
    since: str | None,
    until: str | None,
) -> JournalSnapshot:
    """Bare-metal backend: ask ``anthias-host-agent`` over ``hostcmd``.

    The container can't read the host journal itself — it has no
    ``/var/log/journal`` and no ``systemd-journal`` membership — so
    the read happens in the host agent and comes back through Redis,
    the same split already used for IP addresses and board facts.

    Request/reply, not fire-and-forget: a correlation ID per request
    and a blocking pop on that key. The blocking pop is bounded, so a
    host agent that is dead, too old to understand the JSON payload,
    or simply busy produces a clean 502 rather than a hung worker.
    """
    request_id = uuid.uuid4().hex
    reply_key = journal.result_key(request_id)
    r = connect_to_redis()

    try:
        r.publish(
            'hostcmd',
            journal.build_request(
                request_id,
                count=count,
                unit=unit,
                container=container,
                since=since,
                until=until,
            ),
        )
        reply = r.blpop([reply_key], timeout=journal.REPLY_TIMEOUT_SECONDS)
    except redis.RedisError as error:
        raise JournalUnavailable(
            f'Could not reach the host agent over Redis: {error}'
        ) from error

    # On the timeout path the agent may still be mid-read and about to
    # push a reply nobody will ever pop. No cleanup needed here: the
    # agent sets a TTL on the key precisely for that case.
    if reply is None:
        raise JournalUnavailable(
            'Timed out waiting for the host agent to read the system '
            'journal. Check that anthias-host-agent is running and '
            'up to date.'
        )

    # BLPOP returns (key, value).
    try:
        result = json.loads(reply[1])
    except (ValueError, IndexError, TypeError) as error:
        raise JournalUnavailable(
            'Host agent returned an unreadable journal reply.'
        ) from error

    if not isinstance(result, dict) or not result.get('ok'):
        message = 'The host agent could not read the system journal.'
        if isinstance(result, dict) and result.get('error'):
            message = str(result['error'])
        raise JournalUnavailable(message)

    return JournalSnapshot(
        entries=journal.normalize_payload(str(result.get('payload', ''))),
        source=SOURCE_HOST_AGENT,
        truncated=bool(result.get('truncated')),
        persistent=bool(result.get('persistent')),
    )
