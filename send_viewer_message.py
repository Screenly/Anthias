import json
from argparse import ArgumentParser
from os import getenv
from time import sleep

import redis
from netifaces import AF_INET, ifaddresses, interfaces

VIEWER_CHANNEL = 'anthias.viewer'


def get_portal_url() -> str:
    # 192.168.42.1 is the conventional captive-portal gateway IP that
    # wifi-connect serves its setup AP on; it's only ever reached over a
    # local link to the device, never routed. Hardcoded as a default
    # because PORTAL_GATEWAY is what every Anthias install configures.
    gateway = getenv('PORTAL_GATEWAY', '192.168.42.1')  # NOSONAR
    port = getenv('PORTAL_LISTENING_PORT', None)

    if port is None:
        return gateway
    else:
        return f'{gateway}:{port}'


def get_message(action: str) -> str | None:
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
    return None


def get_ip_addresses() -> list[str]:
    return [
        i['addr']
        for interface_name in interfaces()
        for i in ifaddresses(interface_name).get(AF_INET, [])
        if interface_name in ['eth0', 'wlan0']
        if i.get('addr') is not None
    ]


def is_viewer_subscriber_ready(r: 'redis.Redis') -> bool:
    is_ready = r.get('viewer-subscriber-ready')
    if is_ready is None:
        return False
    else:
        return bool(int(is_ready))


def main() -> None:
    argument_parser = ArgumentParser()
    argument_parser.add_argument(
        '--action',
        required=True,
        choices=('setup_wifi', 'show_splash'),
        help='Specify the message to be sent to the viewer.',
    )
    args = argument_parser.parse_args()
    r = redis.Redis(host='127.0.0.1', decode_responses=True, port=6379, db=0)

    message = get_message(args.action)

    while not is_viewer_subscriber_ready(r):
        sleep(1)

    r.publish(VIEWER_CHANNEL, f'viewer {message}')


if __name__ == '__main__':
    main()
