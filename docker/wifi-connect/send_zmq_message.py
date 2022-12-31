import json
import zmq

from argparse import ArgumentParser
from os import getenv
from time import sleep


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
        return f'{action}'


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

    socket.send_string(f'viewer {get_message(args.action)}')


if __name__ == '__main__':
    main()
