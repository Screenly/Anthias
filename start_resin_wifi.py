#!/usr/bin/python
from __future__ import unicode_literals
from builtins import filter
from builtins import str
import time

import pydbus
import re
import sh
from jinja2 import Template
from netifaces import gateways, interfaces
from os import getenv, path

from lib.utils import generate_perfect_paper_password, get_active_connections


def generate_page(ssid, pswd, address):
    home = getenv('HOME')
    template_path = path.join(home, 'screenly/templates/hotspot.html')
    with open(template_path) as f:
        template = Template(f.read())

    context = {
        'network': ssid,
        'ssid_pswd': pswd,
        'address': address
    }

    with open('/tmp/hotspot.html', 'w') as out_file:
        out_file.write(template.render(context=context))


if __name__ == "__main__":
    bus = pydbus.SystemBus()

    pattern_include = re.compile("wlan*")
    pattern_exclude = re.compile("Anthias-*")

    wireless_connections = get_active_connections(bus)

    if wireless_connections is None:
        exit()

    wireless_connections = [c for c in [c for c in wireless_connections if pattern_include.search(str(c['Devices']))] if not pattern_exclude.search(str(c['Id']))]

    if not gateways().get('default') and list(filter(pattern_include.match, interfaces())):
        if len(wireless_connections) == 0:
            ssid = 'Anthias-{}'.format(generate_perfect_paper_password(pw_length=4, has_symbols=False))
            ssid_password = generate_perfect_paper_password(pw_length=8, has_symbols=False)
            generate_page(ssid, ssid_password, 'screenly.io/wifi')

            wifi_connect = sh.sudo('wifi-connect', '-s', ssid, '-p', ssid_password, '-o', '9090', _bg=True)
    else:
        exit()

    while not gateways().get('default') and list(filter(pattern_include.match, interfaces())):
        time.sleep(.5)
