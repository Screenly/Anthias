#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import unicode_literals
__author__ = "Nash Kaminski"
__license__ = "Dual License: GPLv2 and Commercial License"

import json
import logging
import os
import redis
import socket
import subprocess


REDIS_ARGS = dict(host="127.0.0.1", port=6379, db=0)
# Name of redis channel to listen to
CHANNEL_NAME = b'hostcmd'


def set_ip_addresses():
    rdb = redis.Redis(**REDIS_ARGS)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip_address = s.getsockname()[0]
    rdb.set('ip_addresses', json.dumps([ip_address]))


# Explicit command whitelist for security reasons, keys as bytes objects
CMD_TO_ARGV = {
    b'reboot': ['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'reboot'],
    b'shutdown': ['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'poweroff'],
    b'set_ip_addresses': set_ip_addresses
}


def execute_host_command(cmd_name):
    cmd = CMD_TO_ARGV.get(cmd_name, None)
    if cmd is None:
        logging.warning("Unable to perform host command %s: no such command!", cmd_name)
    elif os.getenv('TESTING'):
        logging.warning("Would have executed %s but not doing so as TESTING is defined", cmd)
    elif cmd_name in [b'reboot', b'shutdown']:
        logging.info("Executing host command %s", cmd_name)
        phandle = subprocess.run(cmd)
        logging.info("Host command %s (%s) returned %s", cmd_name, cmd, phandle.returncode)
    else:
        logging.info('Calling function %s', cmd)
        cmd()


def process_message(message):
    if (message.get('type', '') == 'message' and message.get('channel', b'') == CHANNEL_NAME):
        execute_host_command(message.get('data', b''))
    else:
        logging.info("Received unsolicited message: %s", message)


def subscriber_loop():
    # Connect to redis on localhost and wait for messages
    logging.info("Connecting to redis...")
    rdb = redis.Redis(**REDIS_ARGS)
    pubsub = rdb.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(CHANNEL_NAME)
    rdb.set('host_agent_ready', 'true')
    logging.info("Subscribed to channel %s, ready to process messages", CHANNEL_NAME)
    for message in pubsub.listen():
        process_message(message)


if __name__ == '__main__':
    # Init logging
    logging.basicConfig()
    logging.getLogger().setLevel(logging.INFO)

    # Loop forever processing messages
    subscriber_loop()
