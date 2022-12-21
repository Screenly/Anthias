import json
import zmq

from os import getenv
from time import sleep

def get_portal_url():
    gateway = getenv('PORTAL_GATEWAY', '192.168.42.1')
    port = getenv('PORTAL_LISTENING_PORT', None)

    if port is None:
        return gateway
    else:
        return '{}:{}'.format(gateway, port)

def main():
    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    socket.bind('tcp://0.0.0.0:10001')
    sleep(1)

    data = {
        'network': getenv('PORTAL_SSID'),
        'ssid_pswd': getenv('PORTAL_PASSPHRASE', None),
        'address': get_portal_url(),
    }
    encoded = json.dumps(data)

    socket.send_string('viewer setup-wifi&{}'.format(encoded))


if __name__ == '__main__':
    main()
