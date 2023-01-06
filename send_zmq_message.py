import json
import zmq

from argparse import ArgumentParser
from netifaces import interfaces, ifaddresses, AF_INET
from os import getenv
from time import sleep


def get_portal_url():
    gateway = getenv('PORTAL_GATEWAY', '192.168.42.1')
    port = getenv('PORTAL_LISTENING_PORT', None)

    if port is None:
        return gateway
    else:
        return '{}:{}'.format(gateway, port)

def get_message(action):
    if action == 'setup_wifi':
        data = {
            'network': getenv('PORTAL_SSID'),
            'ssid_pswd': getenv('PORTAL_PASSPHRASE', None),
            'address': get_portal_url(),
        }
        return '{}&{}'.format(action, json.dumps(data))
    elif action == 'show_splash':
        ip_addresses = get_ip_addresses()
        return '{}&{}'.format(action, json.dumps(ip_addresses))


def get_ip_addresses():
    return [
        i['addr']
        for interface_name in interfaces()
        for i in ifaddresses(interface_name).setdefault(AF_INET, [{'addr': None}])
        if interface_name in ['eth0', 'wlan0']
        if i['addr'] is not None
    ]


def main():
    argument_parser = ArgumentParser()
    argument_parser.add_argument(
        '--action',
        required=True,
        choices=('setup_wifi', 'show_splash'),
        help='Specify the ZeroMQ message to be sent.',
    )
    args = argument_parser.parse_args()

    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    socket.bind('tcp://0.0.0.0:10001')
    sleep(1)

    message = get_message(args.action)
    socket.send_string('viewer {}'.format(message))


if __name__ == '__main__':
    main()
