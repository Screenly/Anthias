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
import re
import uuid
from dataclasses import dataclass
from typing import Any

import redis
import requests
from redis.backoff import NoBackoff
from redis.retry import Retry

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

#: Socket headroom over ``REPLY_TIMEOUT_SECONDS`` for the blocking
#: pop. The socket read has to outlive the server-side BLPOP timer:
#: if it expires first, the client abandons a pop that was about to
#: come back empty, and "came back empty" is precisely the outcome
#: this path needs to report.
_REPLY_SOCKET_MARGIN_SECONDS = 5

SOURCE_BALENA = 'balena'
SOURCE_HOST_AGENT = 'host_agent'

#: A container ID as Docker and balena report it — the 64-hex form or
#: its 12-hex short form. Used to tell an ID the caller already has
#: from a service name that still needs resolving.
_CONTAINER_ID_RE = re.compile(r'^[0-9a-f]{12}(?:[0-9a-f]{52})?$')


class JournalUnavailable(Exception):
    """The platform's log backend could not answer this request.

    Distinct from ``journal.JournalRequestError`` (a bad request, 400):
    this is "we asked correctly and the backend didn't deliver", which
    the API surfaces as a 502/503.

    ``user_message`` mirrors the constructor argument so the view can
    return it without going through ``str(exc)``; every message is a
    string composed here for display, never transport exception state
    (that goes to the log instead).
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.user_message: str = message


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
    output, so one normaliser serves both. A ``container`` filter is
    resolved from a name to an ID first — see
    ``_resolve_balena_container_id``. ``since`` / ``until`` need
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
        body['containerId'] = _resolve_balena_container_id(container)
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
        logger.warning(
            'Balena supervisor journal-logs request failed: %s', error
        )
        raise JournalUnavailable(
            'Could not reach the Balena supervisor.'
        ) from error

    try:
        if not response.ok:
            raise JournalUnavailable(
                'Balena supervisor returned '
                f'HTTP {response.status_code} for journal-logs.'
            )
        payload, truncated = _read_capped(response)
    except requests.RequestException as error:
        logger.warning(
            'Balena supervisor journal-logs stream failed: %s', error
        )
        raise JournalUnavailable(
            'Could not read system logs from the Balena supervisor.'
        ) from error
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


def _resolve_balena_container_id(container: str) -> str:
    """Translate a container name into the ID the supervisor expects.

    The ``container`` parameter means the same thing to a caller on
    either platform — the name of a service — but the two backends
    want different things. Bare metal matches journald's
    ``CONTAINER_NAME=`` field directly; the supervisor's
    ``containerId`` becomes ``journalctl -t`` and matches
    ``SYSLOG_IDENTIFIER``. With the default journald log tag this is
    the first 12 characters of the container ID, not the full ID.

    Passing the name straight through would therefore match nothing
    and return an empty 200 — the same request answering on bare metal
    and silently blank on Balena, which is precisely the failure mode
    this feature exists to stamp out. So resolve it, and raise rather
    than return nothing when the name is not a service on this device.

    A value that already looks like a container ID needs no lookup,
    but a full ID still has to be shortened to the journal tag.

    ``/v2/containerId`` predates ``/v2/journal-logs`` in the
    supervisor, so any supervisor new enough for this endpoint at all
    can answer it.
    """
    if _CONTAINER_ID_RE.match(container):
        return container[:12]

    try:
        response = get_balena_supervisor_api_response(
            method='get',
            action='containerId',
            version='v2',
            params={'serviceName': container},
            timeout=_SUPERVISOR_TIMEOUT,
        )
    except requests.RequestException as error:
        # Transport detail goes to the log, never into the response.
        logger.warning(
            'Balena supervisor containerId lookup failed: %s', error
        )
        raise JournalUnavailable(
            'Could not reach the Balena supervisor.'
        ) from error

    if not response.ok:
        raise JournalUnavailable(
            f'No service named {container!r} on this device '
            f'(supervisor returned HTTP {response.status_code}).'
        )

    try:
        container_id = response.json()['containerId']
    except (ValueError, KeyError, TypeError) as error:
        raise JournalUnavailable(
            'The Balena supervisor did not return a container ID for '
            f'{container!r}.'
        ) from error

    if not container_id:
        raise JournalUnavailable(
            f'No running container for service {container!r}.'
        )
    return str(container_id)[:12]


def _read_capped(response: requests.Response) -> tuple[str, bool]:
    """Drain a streamed supervisor response up to the byte cap.

    Streamed rather than buffered because the supervisor hands back
    whatever journalctl produces, and ``count`` bounds lines, not
    bytes — a handful of multi-megabyte messages would otherwise be
    read into memory in full before we got to cap them.
    """
    buffer = bytearray()
    total = 0
    truncated = False
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        buffer += chunk
        if len(buffer) > journal.MAX_PAYLOAD_BYTES:
            # Drop from the FRONT, keeping the newest entries, because
            # journalctl emits oldest-first. Taking the first 4 MB
            # instead would silently throw away exactly the records an
            # operator opened the log to see, and would disagree with
            # the host-agent backend, which keeps its tail.
            del buffer[: len(buffer) - journal.MAX_PAYLOAD_BYTES]
            truncated = True
        if total >= journal.MAX_STREAM_BYTES:
            # Everything else here is bounded; this is too. ``count``
            # caps lines, not bytes, so a supervisor streaming
            # pathological messages could otherwise run until the
            # socket timeout.
            truncated = True
            break
    return bytes(buffer).decode('utf-8', 'replace'), truncated


def _reply_connection() -> redis.Redis:
    """A redis client that can actually carry a 30s blocking pop.

    The shared ``connect_to_redis()`` takes redis-py's defaults, and
    for a BLPOP those defaults are wrong in a way that hides itself:
    ``socket_timeout=5`` with ``Retry(retries=10)``. The socket read
    gives up five seconds into a thirty-second pop, the retry ladder
    silently re-issues it ten more times, and roughly a minute later
    the call raises ``redis.TimeoutError`` instead of returning
    ``None``. Measured at 59.6s on the x86 QA board and 58.2s on a
    Pi, against a host agent that was subscribed but not answering.

    Two consequences, both of which this function exists to remove:

    * ``REPLY_TIMEOUT_SECONDS`` bounded nothing. The real bound was
      the retry ladder, at about twice the intended budget.
    * The ``reply is None`` branch in the caller was unreachable, so
      a stale or wedged host agent surfaced as "Could not reach the
      host agent over Redis" — sending an operator after the broker
      when the broker is healthy.

    These same defaults are already documented as a trap in
    ``lib/cec_client.py`` and ``celery_tasks.py``. They are right for
    the short round trips every other call site makes and wrong for a
    long blocking pop, so widen them here rather than in the shared
    helper.

    Retries are off deliberately. A blocking pop is not idempotent
    the way a GET is: re-issuing it after a socket timeout restarts
    the wait from zero, which is exactly how one bounded request grew
    into a minute-long stall. One attempt, one deadline, one clean
    502.
    """
    return connect_to_redis(
        socket_timeout=(
            journal.REPLY_TIMEOUT_SECONDS + _REPLY_SOCKET_MARGIN_SECONDS
        ),
        retry=Retry(NoBackoff(), 0),
        retry_on_timeout=False,
    )


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
    and a blocking pop on that key. The blocking pop is bounded by
    ``REPLY_TIMEOUT_SECONDS`` — see ``_reply_connection`` for what it
    takes to make that constant the actual bound — so a host agent
    that is dead, too old to understand the JSON payload, or simply
    busy produces a clean 502 rather than a hung worker.
    """
    request_id = uuid.uuid4().hex
    reply_key = journal.result_key(request_id)
    r = _reply_connection()

    try:
        subscribers = r.publish(
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
        if not subscribers:
            # PUBLISH reports how many clients received the message.
            # Zero means nothing is listening on ``hostcmd`` — the
            # host agent is stopped, crashed, or never installed — and
            # no reply is ever coming. Without this check every
            # request holds a worker thread for the full BLPOP
            # timeout, so a log view that retries can stall the whole
            # UI on a device whose agent is simply down.
            raise JournalUnavailable(
                'The anthias-host-agent service is not running, so the '
                'system journal cannot be read. Start it with '
                '`systemctl start anthias-host-agent`.'
            )
        reply = r.blpop([reply_key], timeout=journal.REPLY_TIMEOUT_SECONDS)
    except redis.RedisError as error:
        logger.warning(
            'Host agent journal request over Redis failed: %s', error
        )
        raise JournalUnavailable(
            'Could not reach the host agent over Redis.'
        ) from error

    # Reached when the pop genuinely came back empty, which needs the
    # socket to outlive the BLPOP timer — hence ``_reply_connection``.
    # On redis-py's defaults this branch was dead code and this case
    # surfaced as the Redis error above instead.
    #
    # The agent may still be mid-read here and about to push a reply
    # nobody will ever pop. No cleanup needed: the agent sets a TTL on
    # the key precisely for that case.
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
