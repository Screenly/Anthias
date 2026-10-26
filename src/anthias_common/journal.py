"""Shared contract for the host system-log snapshot API.

Two very different backends answer ``GET /api/v2/system/logs``:

* **Balena** — the container POSTs to the supervisor's
  ``/v2/journal-logs``, which runs ``journalctl`` in the host
  namespace on our behalf.
* **Bare metal** — the container publishes a request on the
  ``hostcmd`` Redis channel; ``anthias_host_agent`` runs
  ``journalctl`` on the host and writes the result back.

Both end up producing journald's ``-o json`` output, so everything
that turns that into the wire shape — the caps, the argument
allowlists, the normaliser, the redactor — lives here and is shared
rather than duplicated per backend.

This module is deliberately dependency-free (stdlib only). The
host agent runs on the host out of the installer venv with
``PYTHONPATH=<repo>/src`` and has no Django / DRF / redis-dependent
imports available to it, same constraint as
``anthias_common.device_helper``.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

#: Lines returned when the caller doesn't ask for a specific count.
DEFAULT_COUNT = 500

#: Hard ceiling on lines, enforced independently on both sides of the
#: wire. The server clamps so a client can't ask for a million lines;
#: the host agent clamps again because anything with access to the
#: local Redis can publish on ``hostcmd``, and ``subscriber_loop`` is
#: single-threaded — a huge read would delay reboot/shutdown handling.
MAX_COUNT = 2000

#: Hard ceiling on the raw journald payload, independent of the line
#: cap: a single entry can carry an arbitrarily long MESSAGE. Keeps
#: one pathological line from putting an unbounded blob into Redis.
MAX_PAYLOAD_BYTES = 4 * 1024 * 1024

#: Absolute ceiling on bytes read off the supervisor stream, as
#: opposed to bytes kept. ``count`` bounds lines, not bytes, so a
#: stream of pathological messages has to be bounded separately or it
#: runs until the socket timeout. Generous enough that a realistic
#: 2000-line read is never cut short.
MAX_STREAM_BYTES = 16 * MAX_PAYLOAD_BYTES

#: How long the host agent's reply stays in Redis. Short on purpose —
#: the only reader is the request that asked for it, and journal
#: contents shouldn't linger in the broker.
RESULT_TTL_SECONDS = 60

#: Wall-clock budget for ``journalctl`` on the host. ``subscriber_loop``
#: is single-threaded; a wedged read must not pin it forever.
SUBPROCESS_TIMEOUT_SECONDS = 20

#: How long the server waits for the host agent's reply. Comfortably
#: larger than the host-side budget so a slow-but-completing read is
#: still delivered rather than racing the client timeout.
REPLY_TIMEOUT_SECONDS = 30

#: ``hostcmd`` command name for a journal snapshot.
JOURNAL_LOGS_COMMAND = 'journal_logs'

#: Where systemd keeps the journal when it is persisting it. The
#: directory's *existence* is not the probe — see
#: ``PERSISTENT_JOURNAL_GLOB``.
PERSISTENT_JOURNAL_DIR = '/var/log/journal'

#: The two places journald can be writing. Which one holds the
#: *freshest* journal file is what tells us whether logs survive a
#: reboot — see ``journal_is_persistent`` in the host agent.
#:
#: Neither simpler probe works on real hardware, and both fail
#: quietly:
#:
#: * "does ``/var/log/journal`` exist?" — Raspberry Pi OS ships that
#:   directory EMPTY alongside
#:   ``/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf``
#:   pinning ``Storage=volatile``, so every Pi would claim its logs
#:   were persistent while journald discarded them each reboot.
#: * "are there any files under ``/var/log/journal``?" — a host that
#:   was persistent once and has since gone volatile keeps its old
#:   files there forever, so it would claim persistence too.
#:
#: Both observed on the QA fleet. A wrong "persistent: true" is
#: precisely the lie this field exists to prevent, so measure which
#: location journald is writing to right now.
PERSISTENT_JOURNAL_GLOB = '/var/log/journal/*/*.journal*'
VOLATILE_JOURNAL_GLOB = '/run/log/journal/*/*.journal*'


# Allowlists for everything that reaches a ``journalctl`` argv.
#
# These arguments are passed as ``--opt=value`` (never ``--opt value``,
# never through a shell), so a value cannot be mistaken for a separate
# flag and there is no shell to inject into. The patterns are belt and
# braces on top of that: they keep a typo'd or hostile value from
# quietly changing what journalctl selects, and they are applied on
# BOTH sides — the API serializer rejects a bad value with a 400, and
# the host agent rejects it again because Redis pub/sub is not an
# authenticated channel.

#: systemd unit names: ``docker.service``, ``user@1000.service``,
#: template instances with escaped characters (``foo\x2dbar.mount``),
#: and glob patterns journalctl accepts (``anthias-*``).
#:
#: The first character is restricted to alphanumerics on purpose: a
#: real unit name never starts with ``-``, and allowing it would let
#: ``--follow`` through as a "unit" — the one thing this endpoint
#: must never run, because it would wedge the host agent's
#: single-threaded subscriber loop.
UNIT_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9@:_.\\*?\-]{0,127}$')

#: Docker container names, per Docker's own
#: ``[a-zA-Z0-9][a-zA-Z0-9_.-]+`` rule.
CONTAINER_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$')

#: ``--since`` / ``--until`` accept a wide, human-friendly grammar:
#: ``2026-10-26 09:30:00``, ``yesterday``, ``-1h``, ``2 days ago``,
#: ``@1761465600``. Allow that character set and nothing else.
TIMESPEC_RE = re.compile(r'^[A-Za-z0-9 :@+\-]{1,64}$')


class JournalRequestError(ValueError):
    """A snapshot request carried a value we refuse to pass on.

    ``user_message`` mirrors the constructor argument so API layers
    can return it without going through ``str(exc)``.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.user_message: str = message


def clamp_count(count: Any) -> int:
    """Coerce a requested line count into ``[1, MAX_COUNT]``.

    Anything unparseable falls back to ``DEFAULT_COUNT`` rather than
    raising: this is the last line of defence on the host side, where
    the caller is a Redis message and the right failure mode is "read
    a sane number of lines", not "drop the request".
    """
    try:
        value = int(count)
    except (TypeError, ValueError):
        return DEFAULT_COUNT
    return max(1, min(value, MAX_COUNT))


def validate_filters(
    *,
    unit: str | None = None,
    container: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> None:
    """Raise ``JournalRequestError`` for any filter we won't forward."""
    checks = (
        ('unit', unit, UNIT_RE),
        ('container', container, CONTAINER_RE),
        ('since', since, TIMESPEC_RE),
        ('until', until, TIMESPEC_RE),
    )
    for name, value, pattern in checks:
        if value in (None, ''):
            continue
        if not pattern.match(str(value)):
            raise JournalRequestError(f'Invalid {name} filter: {value!r}')


def build_journalctl_argv(
    *,
    count: Any = DEFAULT_COUNT,
    unit: str | None = None,
    container: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[str]:
    """Build the host-side ``journalctl`` command line.

    ``--opt=value`` form throughout, so no value can be re-read as a
    flag. ``-o json`` is what gives bare metal the same payload shape
    the Balena supervisor returns for ``format: 'json'``, which is why
    one normaliser can serve both backends.

    The container filter uses journald's ``CONTAINER_NAME=`` match
    field rather than a unit: the Anthias services log through Docker's
    ``journald`` driver, so their records are tagged with that field
    and are not attributable to a systemd unit of their own.
    """
    validate_filters(unit=unit, container=container, since=since, until=until)

    argv = [
        'journalctl',
        '--no-pager',
        '--output=json',
        '--all',
        f'--lines={clamp_count(count)}',
    ]
    if unit:
        argv.append(f'--unit={unit}')
    if since:
        argv.append(f'--since={since}')
    if until:
        argv.append(f'--until={until}')
    if container:
        # A match expression, not an option — it must come last so it
        # isn't consumed as an option argument.
        argv.append(f'CONTAINER_NAME={container}')
    return argv


def result_key(request_id: str) -> str:
    """Redis key carrying the reply for one snapshot request.

    Used as a *list* and read with ``BLPOP``: that is the repo's
    existing request/reply primitive (see ``anthias.reply.<id>`` on
    the viewer bus) and it has no subscribe-before-publish race the
    way a completion pub/sub would. The correlation ID in the key is
    what keeps concurrent requests from clobbering each other.
    """
    return f'host:journal:{request_id}'


def build_request(
    request_id: str,
    *,
    count: Any = DEFAULT_COUNT,
    unit: str | None = None,
    container: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> str:
    """Serialise the ``hostcmd`` payload for a snapshot request."""
    args: dict[str, Any] = {'count': clamp_count(count)}
    for name, value in (
        ('unit', unit),
        ('container', container),
        ('since', since),
        ('until', until),
    ):
        if value:
            args[name] = value
    return json.dumps(
        {
            'cmd': JOURNAL_LOGS_COMMAND,
            'id': request_id,
            'args': args,
        }
    )


# Redaction.
#
# The journal is a host-wide firehose and the device's own install
# path puts secrets through it: ``docker-compose.yml.tmpl`` already
# carries comments about keeping WPA passphrases out of journald, and
# wpa_supplicant / NetworkManager / wifi-connect all log key material
# on a bad day. The snapshot endpoint is authenticated, but Anthias
# has a single operator account and an operator reading logs has no
# reason to need the plaintext, so scrub the obvious shapes on the
# way out.
#
# This is a safety net, not a guarantee: it cannot know about every
# secret a third-party unit might print. It is deliberately
# conservative — it blanks the *value*, keeps the key visible, so a
# redacted line still tells you what happened.
_REDACTION_PATTERNS = (
    # key=value / key: value, with or without quotes.
    #
    # The leading ``[\w.-]*`` is load-bearing: the real-world offenders
    # are prefixed (``wpa_passphrase=``, ``NM_PSK=``) and a plain
    # ``\b`` would not fire between ``_`` and ``p``.
    #
    # The ``(?<![\w.-])`` in front of it is what keeps that affordable.
    # Without it this pattern is quadratic: inside a long run of word
    # characters with no ``=``, every offset is a candidate start and
    # every prefix length is retried against the alternation. Measured
    # in-repo at 0.4s for 4 KB, 6.3s for 16 KB — and journald's default
    # LineMax is 48 KB, so ONE pathological line would pin a request
    # worker for the better part of a minute. The lookbehind means only
    # a position that actually begins a token can start a match, which
    # takes it back to linear.
    re.compile(
        r'(?i)(?<![\w.\-])([\w.\-]*(?:pass(?:word|phrase)?|psk|secret|'
        r'api[_-]?key|token|auth[_-]?key|private[_-]?key))(\s*[=:]\s*)'
        r'("[^"]*"|\'[^\']*\'|\S+)'
    ),
    # Authorization: Bearer <token> / Basic <blob>.
    re.compile(r'(?i)\b(authorization)(\s*:\s*)(\S+\s+\S+|\S+)'),
)

REDACTED = '[redacted]'


def redact(message: str) -> str:
    """Blank secret-looking values in one journal message."""
    for pattern in _REDACTION_PATTERNS:
        message = pattern.sub(
            lambda m: f'{m.group(1)}{m.group(2)}{REDACTED}', message
        )
    return message


def _decode_message(value: Any) -> str:
    """Render a journald ``MESSAGE`` field as text.

    journald emits a non-UTF-8 message as a JSON array of byte values
    instead of a string, and ``_SOURCE_*`` style fields can be absent
    entirely. Normalise all three shapes so the wire type is always
    ``string``.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        try:
            return bytes(bytearray(value)).decode('utf-8', 'replace')
        except (TypeError, ValueError):
            return ''
    if value is None:
        return ''
    return str(value)


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _timestamp(entry: dict[str, Any]) -> str | None:
    """RFC 3339 UTC timestamp from journald's microsecond fields."""
    usec = _as_int(
        entry.get('__REALTIME_TIMESTAMP')
        or entry.get('_SOURCE_REALTIME_TIMESTAMP')
    )
    if usec is None:
        return None

    return (
        datetime.fromtimestamp(usec / 1_000_000, tz=UTC)
        .isoformat()
        .replace('+00:00', 'Z')
    )


def normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """One journald ``-o json`` record → one wire entry."""
    return {
        'timestamp': _timestamp(entry),
        'priority': _as_int(entry.get('PRIORITY')),
        'unit': entry.get('_SYSTEMD_UNIT') or entry.get('UNIT') or None,
        'identifier': entry.get('SYSLOG_IDENTIFIER') or None,
        'hostname': entry.get('_HOSTNAME') or None,
        'container_name': entry.get('CONTAINER_NAME') or None,
        'message': redact(_decode_message(entry.get('MESSAGE'))),
    }


def normalize_payload(payload: str) -> list[dict[str, Any]]:
    """Parse a journald ``-o json`` stream into wire entries.

    Tolerates a truncated final line: both backends cap the payload by
    bytes, which can cut the last record mid-object. A line that
    doesn't parse is dropped rather than failing the whole snapshot —
    losing one entry at the boundary beats 500-ing a log viewer.
    """
    entries = []
    for line in payload.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            entries.append(normalize_entry(record))
    return entries
