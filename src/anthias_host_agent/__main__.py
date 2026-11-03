#!/usr/bin/env python3

__author__ = 'Nash Kaminski'
__license__ = 'Dual License: GPLv2 and Commercial License'

import glob
import ipaddress
import json
import logging
import os
import selectors
import subprocess
import time
from collections.abc import Callable
from typing import Any

import redis
import requests
from tenacity import (
    RetryError,
    Retrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

# Shared with anthias_common.board's in-container fallback — the
# model-string → subtype table must not drift between the host-side
# publisher (this service) and the container-side resolver, so it
# lives in the dependency-free anthias_common.device_helper. The
# systemd unit runs this module with PYTHONPATH=<repo>/src (see
# ansible/roles/anthias/templates/anthias-host-agent.service), which
# is what makes the import resolvable from the host venv.
from anthias_common.device_helper import (
    detect_board_subtype,
    read_device_tree_model,
)

# Same deal for the journal snapshot contract: the caps, the
# journalctl argv builder and the Redis key naming are shared with the
# in-container reader (anthias_server.lib.system_logs), so neither
# side can drift from the other.
from anthias_common.journal import (
    JOURNAL_LOGS_COMMAND,
    MAX_PAYLOAD_BYTES,
    PERSISTENT_JOURNAL_GLOB,
    RESULT_TTL_SECONDS,
    SUBPROCESS_TIMEOUT_SECONDS,
    VOLATILE_JOURNAL_GLOB,
    JournalRequestError,
    build_journalctl_argv,
    result_key,
)
from anthias_host_agent.ifaddrs import interface_addresses

logger = logging.getLogger(__name__)

REDIS_HOST = '127.0.0.1'
REDIS_PORT = 6379
REDIS_DB = 0
# Name of redis channel to listen to
CHANNEL_NAME = 'hostcmd'
SUPPORTED_INTERFACES = (
    'wlan',
    'eth',
    'wlp',
    'enp',
    'eno',
    'ens',
    # `endN` is the systemd predictable name for on-board NICs whose
    # device-tree node doesn't expose ACPI/PCI numbering — what
    # Rockchip / Allwinner / Amlogic SBCs typically report (e.g. the
    # Rock Pi 4's GMAC comes up as `end0`). Without this prefix, the
    # splash page on every arm64 install would sit on
    # "Detecting network…" indefinitely.
    'end',
)

# Cloudflare's 1.1.1.1 public DNS anycast, used purely as an Internet
# reachability probe before reading the host's interface addresses.
# Public anycast, not a private/internal address.
INTERNET_PROBE_URL = 'https://1.1.1.1'  # NOSONAR


def get_ip_addresses() -> list[str]:
    try:
        addresses_by_interface = interface_addresses()
    except OSError:
        # Opening and reading a netlink socket can fail where the old
        # netifaces call couldn't — fd exhaustion, ENOBUFS under memory
        # pressure, or a wedged dump hitting the timeout. This runs
        # inside the pubsub handler, and nothing up the stack catches:
        # an exception here ends subscriber_loop and takes reboot /
        # shutdown handling down with it until systemd restarts the
        # unit. Worse, set_ip_addresses has already flipped
        # `ip_addresses_ready` to true by this point, so dying here
        # leaves consumers reading "ready" against a key that was never
        # written. Report no addresses and let the next
        # set_ip_addresses request try again.
        logger.warning(
            'Unable to read host interface addresses over rtnetlink',
            exc_info=True,
        )
        return []

    return [
        address
        for interface, addresses in addresses_by_interface.items()
        if interface.startswith(SUPPORTED_INTERFACES)
        for address in addresses
        if not ipaddress.ip_address(address).is_link_local
    ]


def set_ip_addresses(probe_internet: bool = True) -> None:
    """Publish the host's interface addresses to Redis.

    ``probe_internet`` is what the two callers differ on. The splash
    page wants the addresses a user can reach the device on, and on a
    cold boot those are not final until DHCP has settled — so it waits
    for an outbound request to succeed first, and accepts taking ~10s
    to answer when the device is offline.

    The WiFi-setup poll wants the opposite. It is asking whether one
    specific address (the setup portal's own gateway) is on an
    interface right now, it runs for as long as a device is offline,
    and it must not make this service spend ten seconds out of every
    fifteen inside a retrying HTTP call — ``subscriber_loop`` is
    single-threaded, and reboot/shutdown commands queue behind
    whatever it is doing.
    """
    rdb = redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True
    )

    rdb.set('ip_addresses_ready', 'false')

    if not probe_internet:
        # Addresses first, then the readiness flag: a consumer that
        # gates on ``ip_addresses_ready`` should never be told the
        # answer is ready and then read the previous run's list.
        addresses = get_ip_addresses()
        rdb.set('ip_addresses', json.dumps(addresses))
        rdb.set('ip_addresses_ready', 'true')
        return

    try:
        for attempt in Retrying(
            stop=stop_after_attempt(10),
            wait=wait_fixed(1),
            before_sleep=before_sleep_log(
                logging.getLogger(), logging.WARNING, exc_info=True
            ),
        ):
            with attempt:
                response = requests.get(INTERNET_PROBE_URL, timeout=5)
                response.raise_for_status()
    except RetryError:
        logger.warning(
            'Unable to connect to the Internet. '
            'Proceeding with the current IP addresses available.'
        )

    rdb.set('ip_addresses_ready', 'true')

    ip_addresses = get_ip_addresses()
    rdb.set('ip_addresses', json.dumps(ip_addresses))


def _newest_journal_mtime(pattern: str) -> float | None:
    """mtime of the most recently written journal file, or None."""
    mtimes = []
    for path in glob.glob(pattern):
        try:
            mtimes.append(os.path.getmtime(path))
        except OSError:
            # Rotated out from under us between glob and stat.
            continue
    return max(mtimes) if mtimes else None


def journal_is_persistent() -> bool:
    """Does this host keep the journal across reboots?

    Measured, not inferred: whichever of ``/var/log/journal`` and
    ``/run/log/journal`` holds the freshest journal file is where
    journald is writing right now. Both cheaper probes give a wrong
    "yes" on hardware we actually ship on — see the globs in
    ``anthias_common.journal`` for the two cases.

    Reported rather than assumed because an install predating the
    Ansible persistence task still has a volatile journal, and the
    API has to be able to tell an operator that what they are reading
    is current-boot-only.
    """
    persistent = _newest_journal_mtime(PERSISTENT_JOURNAL_GLOB)
    if persistent is None:
        return False
    volatile = _newest_journal_mtime(VOLATILE_JOURNAL_GLOB)
    return volatile is None or persistent >= volatile


def _system_journal_files() -> list[str]:
    """Journal files that hold SYSTEM records, in either location.

    ``user-<uid>.journal`` is excluded deliberately: journald makes a
    user's own journal readable by that user, so including it would
    make the readability probe below pass on exactly the host where
    the system journal is hidden.
    """
    return [
        path
        for pattern in (PERSISTENT_JOURNAL_GLOB, VOLATILE_JOURNAL_GLOB)
        for path in glob.glob(pattern)
        if not os.path.basename(path).startswith('user-')
    ]


def system_journal_is_readable() -> bool:
    """Can this process actually read the host's system journal?

    There is no usable signal from journalctl itself. It never exits
    non-zero for a caller that may not read the system journal: it
    returns whatever that caller CAN see — its own user journal — and
    exits 0. It does print a hint on stderr, but only for the human
    output formats; measured on the QA rig, ``--output=json`` (what
    this agent uses, because it is the format that matches the Balena
    supervisor's) produces an EMPTY stderr and return code 0 whether
    or not the system journal was visible. A ``-u docker.service``
    read came back with zero lines, no error, nothing to distinguish
    it from a unit that simply had not logged.

    So ask the kernel instead. ``os.access`` honours POSIX ACLs as
    well as group membership, which matters because journald grants
    the ``adm`` group access by ACL rather than by file group.

    A host with no system journal files at all answers True: we
    cannot tell, and refusing the request would be worse than
    returning whatever journalctl finds.
    """
    candidates = _system_journal_files()
    if not candidates:
        return True
    return any(os.access(path, os.R_OK) for path in candidates)


def _reply(
    rdb: 'redis.Redis', request_id: str, payload: dict[str, Any]
) -> None:
    """Deliver one journal reply to the waiting container.

    RPUSH + EXPIRE onto the correlation-ID key, which the server is
    sitting on with BLPOP. A list rather than a pub/sub completion
    message because a blocking pop has no subscribe-before-publish
    race, and it is what the repo already uses for request/reply on
    the viewer bus.

    The TTL matters in the failure case: if the requesting worker has
    already given up and gone away, nobody ever pops this and it would
    otherwise sit in Redis holding journal text indefinitely.
    """
    key = result_key(request_id)
    try:
        rdb.rpush(key, json.dumps(payload))
        rdb.expire(key, RESULT_TTL_SECONDS)
    except redis.RedisError:
        # Nothing useful left to do — the requester's BLPOP will time
        # out and it will report the snapshot as unavailable.
        logger.warning(
            'Unable to deliver journal reply %s to redis',
            request_id,
            exc_info=True,
        )


def _run_journalctl(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Read a snapshot with bounded buffers and one wall-clock deadline.

    --all preserves long MESSAGE fields, so capturing all output before
    applying the byte cap could exhaust memory. Drain both pipes while
    retaining only the newest stdout bytes and the first 500 stderr
    bytes. One extra stdout byte lets the caller detect truncation.
    """
    deadline = time.monotonic() + SUBPROCESS_TIMEOUT_SECONDS
    stdout = bytearray()
    stderr = bytearray()
    with subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as process:
        try:
            with selectors.DefaultSelector() as selector:
                assert process.stdout is not None
                assert process.stderr is not None
                selector.register(process.stdout, selectors.EVENT_READ, True)
                selector.register(process.stderr, selectors.EVENT_READ, False)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(
                            argv, SUBPROCESS_TIMEOUT_SECONDS
                        )
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fd, 64 * 1024)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        elif key.data:
                            stdout.extend(chunk)
                            overflow = len(stdout) - (MAX_PAYLOAD_BYTES + 1)
                            if overflow > 0:
                                del stdout[:overflow]
                        else:
                            stderr.extend(chunk[: max(0, 500 - len(stderr))])
            returncode = process.wait(
                timeout=max(0, deadline - time.monotonic())
            )
        except BaseException:
            # Reap the exact child on timeouts and read failures as well.
            process.kill()
            process.wait()
            raise
    return subprocess.CompletedProcess(
        argv,
        returncode,
        stdout.decode('utf-8', 'replace'),
        stderr.decode('utf-8', 'replace'),
    )


def collect_journal_logs(request_id: str, args: dict[str, Any]) -> None:
    """Run a bounded ``journalctl`` snapshot and reply over Redis.

    Every exit path writes exactly one reply, including the error
    paths, so the container's BLPOP resolves promptly instead of
    waiting out its timeout on a request that already failed.

    **This must never block indefinitely.** ``subscriber_loop`` is
    single-threaded — reboot and shutdown queue behind whatever runs
    here — which is why the snapshot is capped by lines, capped by
    bytes, and hard-capped in wall-clock by the subprocess timeout,
    and why ``--follow`` is not and cannot be offered.
    """
    rdb = redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True
    )

    try:
        argv = build_journalctl_argv(
            count=args.get('count'),
            unit=args.get('unit'),
            container=args.get('container'),
            since=args.get('since'),
            until=args.get('until'),
        )
    except JournalRequestError as error:
        # Re-validated here rather than trusted from the request: the
        # hostcmd channel is unauthenticated local Redis, so the API
        # serializer's 400 is not the only gate these values pass.
        logger.warning('Rejecting journal request %s: %s', request_id, error)
        _reply(rdb, request_id, {'ok': False, 'error': str(error)})
        return

    if not system_journal_is_readable():
        # Fail fast and loudly. journalctl would exit 0 here and hand
        # back the service account's own user journal with every
        # system record silently missing — a log view that looks
        # populated and omits everything an operator came for.
        logger.warning(
            'Refusing journal request %s: the host agent cannot read '
            'the system journal. The unit needs '
            'SupplementaryGroups=systemd-journal.',
            request_id,
        )
        _reply(
            rdb,
            request_id,
            {
                'ok': False,
                'error': (
                    'The host agent cannot read the system journal '
                    '(insufficient permissions). Update '
                    'anthias-host-agent.service so it runs with '
                    'SupplementaryGroups=systemd-journal, then restart '
                    'it.'
                ),
            },
        )
        return

    logger.info('Collecting journal snapshot %s: %s', request_id, argv)
    try:
        completed = _run_journalctl(argv)
    except subprocess.TimeoutExpired:
        logger.warning(
            'journalctl timed out after %ss for request %s',
            SUBPROCESS_TIMEOUT_SECONDS,
            request_id,
        )
        _reply(
            rdb,
            request_id,
            {'ok': False, 'error': 'Timed out reading the system journal.'},
        )
        return
    except OSError as error:
        # No journalctl on PATH, or exec failed. Real on a
        # non-systemd host; report it rather than dying inside the
        # pubsub handler and taking subscriber_loop down with it.
        logger.warning('Unable to execute journalctl: %s', error)
        _reply(
            rdb,
            request_id,
            {
                'ok': False,
                'error': 'journalctl is not available on this host.',
            },
        )
        return

    if completed.returncode != 0:
        stderr_text = (completed.stderr or '').strip()[:500]
        logger.warning(
            'journalctl exited %s for request %s: %s',
            completed.returncode,
            request_id,
            stderr_text,
        )
        _reply(
            rdb,
            request_id,
            {
                'ok': False,
                'error': stderr_text or 'journalctl failed with no output.',
            },
        )
        return

    payload = completed.stdout or ''
    encoded = payload.encode('utf-8')
    truncated = len(encoded) > MAX_PAYLOAD_BYTES
    if truncated:
        # Keep the newest entries: journalctl -n emits oldest-first,
        # so trim from the front. The decoder tolerates a partial
        # leading line and drops it.
        payload = encoded[-MAX_PAYLOAD_BYTES:].decode('utf-8', 'replace')

    _reply(
        rdb,
        request_id,
        {
            'ok': True,
            'payload': payload,
            'truncated': truncated,
            'persistent': journal_is_persistent(),
        },
    )


# Explicit command whitelist for security reasons.
CMD_TO_ARGV: dict[str, list[str] | Callable[[], None]] = {
    'reboot': ['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'reboot'],
    'shutdown': ['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'poweroff'],
    'set_ip_addresses': set_ip_addresses,
    # The same refresh without the internet probe -- see the docstring
    # on set_ip_addresses for why the WiFi-setup poll needs its own
    # entry point rather than reusing the one above.
    'set_ip_addresses_fast': lambda: set_ip_addresses(probe_internet=False),
}


def parse_host_command(
    payload: str,
) -> tuple[str, str | None, dict[str, Any]]:
    """Split a ``hostcmd`` message into (name, correlation id, args).

    Two wire shapes are accepted, on purpose:

    * **Bare string** (``'reboot'``, ``'set_ip_addresses'``) — the
      original fire-and-forget form. Still the only shape the
      long-standing callers publish, and a container running an older
      image than the host agent keeps working. Not going anywhere.
    * **JSON object** (``{"cmd": …, "id": …, "args": {…}}``) — needed
      by anything with parameters and a reply, i.e. journal snapshots.
      The correlation ID is what keeps two concurrent requests from
      reading each other's results.

    A payload that merely looks like JSON but isn't falls back to the
    bare-string path, where it fails the normal "no such command"
    way rather than raising out of the pubsub handler.
    """
    stripped = payload.strip()
    if stripped.startswith('{'):
        try:
            message = json.loads(stripped)
        except ValueError:
            message = None
        if isinstance(message, dict):
            args = message.get('args')
            return (
                str(message.get('cmd', '')),
                message.get('id'),
                args if isinstance(args, dict) else {},
            )
    return payload, None, {}


def execute_host_command(payload: str) -> None:
    cmd_name, request_id, args = parse_host_command(payload)

    if cmd_name == JOURNAL_LOGS_COMMAND:
        if not request_id:
            logger.warning('Ignoring journal request with no correlation id')
            return
        # Deliberately not gated on TESTING the way reboot/shutdown
        # are: that gate exists to stop destructive commands firing,
        # and reading the journal is read-only.
        collect_journal_logs(str(request_id), args)
        return

    cmd = CMD_TO_ARGV.get(cmd_name, None)
    if cmd is None:
        logger.warning(
            'Unable to perform host command %s: no such command!', cmd_name
        )
    elif os.getenv('TESTING'):
        logger.warning(
            'Would have executed %s but not doing so as TESTING is defined',
            cmd,
        )
    elif cmd_name in ['reboot', 'shutdown']:
        logger.info('Executing host command %s', cmd_name)
        if not isinstance(cmd, list):
            raise TypeError(f'Expected list for {cmd_name}, got {type(cmd)}')
        phandle = subprocess.run(cmd, check=False)
        logger.info(
            'Host command %s (%s) returned %s',
            cmd_name,
            cmd,
            phandle.returncode,
        )
    else:
        logger.info('Calling function %s', cmd)
        if not callable(cmd):
            raise TypeError(
                f'Expected callable for {cmd_name}, got {type(cmd)}'
            )
        cmd()


def process_message(message: dict[str, Any]) -> None:
    if (
        message.get('type', '') == 'message'
        and message.get('channel', '') == CHANNEL_NAME
    ):
        execute_host_command(message.get('data', ''))
    else:
        logger.info('Received unsolicited message: %s', message)


def detect_total_mem_kb() -> int | None:
    """Return the host's ``MemTotal`` (kibibytes) from ``/proc/meminfo``.

    Anthias's codec gate uses this to refuse 1080p+ uploads on devices
    that can't keep them in physical RAM alongside a QtWebEngine
    renderer. Returning ``None`` for an unreadable / unparseable
    ``/proc/meminfo`` is treated by the consumer as "unknown, don't
    restrict" — the gate would rather over-accept than reject a
    legitimate upload on a host where we can't measure RAM.

    The host_agent runs on the host (outside the container) so it
    reads the *physical* host's MemTotal, not a per-container cgroup
    limit. Inside a container, ``/proc/meminfo`` reports the same
    host-wide value too (cgroup memory accounting doesn't rewrite it),
    so we could read this from the server, but routing through the
    host_agent + Redis keeps the existing pattern: anything board /
    host-shape-related lives in host_agent and lands in Redis under
    ``host:*``.
    """
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if not line.startswith('MemTotal:'):
                    continue
                # Format: ``MemTotal:       12345678 kB``
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        return int(parts[1])
                    except ValueError:
                        return None
    except OSError:
        return None
    return None


def set_total_mem_kb(rdb: 'redis.Redis') -> None:
    """Publish the host's MemTotal (kibibytes) to Redis.

    Written before ``host_agent_ready`` flips so a consumer waiting on
    readiness never observes a stale missing key. An unreadable
    ``/proc/meminfo`` writes the empty string — the server-side
    reader (``anthias_common.board.get_total_mem_kb``) treats empty
    / missing identically as "unknown".
    """
    value = detect_total_mem_kb()
    rdb.set('host:total_mem_kb', '' if value is None else str(value))
    if value is None:
        logger.warning(
            'Could not read MemTotal from /proc/meminfo; low-RAM '
            'detection will fall back to "unknown" and not enforce '
            'the resolution cap.'
        )
    else:
        logger.info('Published host total_mem_kb=%s to redis', value)


def set_board_subtype(rdb: 'redis.Redis') -> None:
    """Publish the host's board subtype to Redis.

    Server + viewer read ``host:board_subtype`` to upgrade the
    catch-all ``arm64`` DEVICE_TYPE into a board-specific matrix
    key when one is detected. Written before
    ``host_agent_ready`` flips so consumers don't read a stale
    None when they wait on the readiness flag.

    On an unknown board (or a host without a device tree) the key
    is set to the empty string. The server-side reader treats
    empty / missing identically — falls back to the static
    DEVICE_TYPE matrix entry — but writing the empty string still
    distinguishes "host_agent ran and didn't recognise this board"
    from "host_agent never ran".
    """
    subtype = detect_board_subtype() or ''
    rdb.set('host:board_subtype', subtype)
    if subtype:
        logger.info('Published board subtype %r to redis', subtype)
    else:
        logger.info(
            'No known board subtype for /proc/device-tree/model — '
            'staying on DEVICE_TYPE-derived envelope'
        )


def set_device_model(rdb: 'redis.Redis') -> None:
    """Publish the host's device-tree board model to Redis.

    The server renders ``host:device_model`` as the System Info card's
    device label. It cannot read the tree itself — Docker masks
    ``/sys/firmware`` in unprivileged containers — so without this the
    card falls through to 'Generic aarch64 Device' on every non-Pi
    SBC. Written before ``host_agent_ready`` flips, like the other
    host facts.

    x86 hosts have no device tree and publish the empty string; the
    reader treats empty / missing identically and keeps its existing
    DMI-derived label.
    """
    model = read_device_tree_model()
    rdb.set('host:device_model', model)
    if model:
        logger.info('Published host device model %r to redis', model)
    else:
        logger.info(
            'No /proc/device-tree/model on this host — device label '
            'falls back to DMI / CPU / generic'
        )


def subscriber_loop() -> None:
    # On first boot the redis container may not yet accept connections;
    # retry quietly instead of crashing the unit on every attempt.
    logger.info('Connecting to redis...')
    for attempt in Retrying(
        retry=retry_if_exception_type(redis.ConnectionError),
        wait=wait_fixed(5),
        stop=stop_after_attempt(60),
        before_sleep=before_sleep_log(logging.getLogger(), logging.WARNING),
        reraise=True,
    ):
        with attempt:
            rdb = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                decode_responses=True,
            )
            pubsub = rdb.pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(CHANNEL_NAME)
    set_board_subtype(rdb)
    set_device_model(rdb)
    set_total_mem_kb(rdb)
    rdb.set('host_agent_ready', 'true')
    logger.info(
        'Subscribed to channel %s, ready to process messages', CHANNEL_NAME
    )
    for message in pubsub.listen():
        process_message(message)


if __name__ == '__main__':
    # Init logging
    logging.basicConfig()
    logging.getLogger().setLevel(logging.INFO)

    # Loop forever processing messages
    subscriber_loop()
