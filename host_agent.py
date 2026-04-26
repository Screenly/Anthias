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


def subscriber_loop() -> None:
    # On first boot the redis container may not yet accept connections;
    # retry quietly instead of crashing the unit on every attempt.
    logging.info('Connecting to redis...')
    for attempt in Retrying(
        retry=retry_if_exception_type(redis.exceptions.ConnectionError),
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
