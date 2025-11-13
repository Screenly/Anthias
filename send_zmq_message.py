from __future__ import unicode_literals

import json
from argparse import ArgumentParser
from os import getenv
from time import sleep

import redis
import zmq
from netifaces import AF_INET, ifaddresses, interfaces


def get_portal_url():
    gateway = getenv('PORTAL_GATEWAY', '192.168.42.1')
    port = getenv('PORTAL_LISTENING_PORT', None)

    if port is None:
        return gateway
    else:
        return f'{gateway}:{port}'


def get_message(action):
    if action == 'setup_wifi':
        data = {
            'network': getenv('PORTAL_SSID'),
            'ssid_pswd': getenv('PORTAL_PASSPHRASE', None),
            'address': get_portal_url(),
        }
        return f'{action}&{json.dumps(data)}'
    elif action == 'show_splash':
        ip_addresses = get_ip_addresses()
        return f'{action}&{json.dumps(ip_addresses)}'


def get_ip_addresses():
    return [
        i['addr']
        for interface_name in interfaces()
        for i in ifaddresses(interface_name).setdefault(
            AF_INET, [{'addr': None}]
        )
        if interface_name in ['eth0', 'wlan0']
        if i['addr'] is not None
    ]


def is_viewer_subscriber_ready(r):
    is_ready = r.get('viewer-subscriber-ready')
    if is_ready is None:
        return False
    else:
        return bool(int(is_ready))


def main():
    argument_parser = ArgumentParser()
    argument_parser.add_argument(
        '--action',
        required=True,
        choices=('setup_wifi', 'show_splash'),
        help='Specify the ZeroMQ message to be sent.',
    )
    args = argument_parser.parse_args()
    r = redis.Redis(host='127.0.0.1', decode_responses=True, port=6379, db=0)

    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    socket.bind('tcp://0.0.0.0:10001')
    sleep(1)

    message = get_message(args.action)

    while not is_viewer_subscriber_ready(r):
        sleep(1)
        continue

    socket.send_string(f'viewer {message}')


if __name__ == '__main__':
    main()
