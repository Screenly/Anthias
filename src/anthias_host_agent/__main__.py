#!/usr/bin/env python3
# -*- coding: utf-8 -*-

__author__ = 'Nash Kaminski'
__license__ = 'Dual License: GPLv2 and Commercial License'

import ipaddress
import json
import logging
import os
import subprocess
from typing import Any, Callable

import netifaces
import redis
import requests
from tenacity import (
    before_sleep_log,
    retry_if_exception_type,
    RetryError,
    Retrying,
    stop_after_attempt,
    wait_fixed,
)

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
    return [
        ip['addr']
        for interface in netifaces.interfaces()
        if interface.startswith(SUPPORTED_INTERFACES)
        for ip in (
            netifaces.ifaddresses(interface).get(netifaces.AF_INET, [])
            + netifaces.ifaddresses(interface).get(netifaces.AF_INET6, [])
        )
        if not ipaddress.ip_address(ip['addr']).is_link_local
    ]


def set_ip_addresses() -> None:
    rdb = redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True
    )

    rdb.set('ip_addresses_ready', 'false')

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
        logging.warning(
            'Unable to connect to the Internet. '
            'Proceeding with the current IP addresses available.'
        )

    rdb.set('ip_addresses_ready', 'true')

    ip_addresses = get_ip_addresses()
    rdb.set('ip_addresses', json.dumps(ip_addresses))


# Explicit command whitelist for security reasons.
CMD_TO_ARGV: dict[str, list[str] | Callable[[], None]] = {
    'reboot': ['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'reboot'],
    'shutdown': ['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'poweroff'],
    'set_ip_addresses': set_ip_addresses,
}


def execute_host_command(cmd_name: str) -> None:
    cmd = CMD_TO_ARGV.get(cmd_name, None)
    if cmd is None:
        logging.warning(
            'Unable to perform host command %s: no such command!', cmd_name
        )
    elif os.getenv('TESTING'):
        logging.warning(
            'Would have executed %s but not doing so as TESTING is defined',
            cmd,
        )
    elif cmd_name in ['reboot', 'shutdown']:
        logging.info('Executing host command %s', cmd_name)
        if not isinstance(cmd, list):
            raise TypeError(f'Expected list for {cmd_name}, got {type(cmd)}')
        phandle = subprocess.run(cmd)
        logging.info(
            'Host command %s (%s) returned %s',
            cmd_name,
            cmd,
            phandle.returncode,
        )
    else:
        logging.info('Calling function %s', cmd)
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
        logging.info('Received unsolicited message: %s', message)


def detect_board_subtype() -> str | None:
    """Identify the SBC by reading ``/proc/device-tree/model``.

    Returns a stable short token (e.g. ``'rockpi4'``) when the model
    string matches a known board, or ``None`` for unknown boards /
    hosts without a device tree. The viewer reads the value the
    publisher writes (``host:board_subtype``) to pick the right
    ``--hwdec=`` for the SoC.

    Anthias's ``bin/install.sh`` writes ``DEVICE_TYPE=arm64`` for
    every aarch64 SBC it doesn't recognise as a Pi. Most such boards
    have no upstream-mpv HW decode path, but a few (Rock Pi 4 with
    RK3399's Hantro VPU via v4l2m2m) do. Knowing which is which at
    runtime lets the viewer pick the right ``--hwdec=`` value
    without forcing operators to manually distinguish images.

    Host_agent runs on the host (not in a container) so it can
    read the device tree directly — the alternative (mounting
    ``/proc/device-tree`` into every container) is heavier and
    doesn't compose well with balena.
    """
    try:
        with open('/proc/device-tree/model', 'rb') as f:
            # Kernel writes a null-terminated UTF-8 string.
            model = f.read().decode('utf-8', 'replace').strip('\x00 \n\t')
    except OSError:
        return None
    if not model:
        return None
    model_low = model.lower()
    # "Radxa ROCK Pi 4B" (and 4A / 4C variants — all RK3399).
    if 'rock pi 4' in model_low:
        return 'rockpi4'
    return None


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
        logging.warning(
            'Could not read MemTotal from /proc/meminfo; low-RAM '
            'detection will fall back to "unknown" and not enforce '
            'the resolution cap.'
        )
    else:
        logging.info('Published host total_mem_kb=%s to redis', value)


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
        logging.info('Published board subtype %r to redis', subtype)
    else:
        logging.info(
            'No known board subtype for /proc/device-tree/model — '
            'staying on DEVICE_TYPE-derived envelope'
        )


def subscriber_loop() -> None:
    # On first boot the redis container may not yet accept connections;
    # retry quietly instead of crashing the unit on every attempt.
    logging.info('Connecting to redis...')
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
    set_total_mem_kb(rdb)
    rdb.set('host_agent_ready', 'true')
    logging.info(
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
