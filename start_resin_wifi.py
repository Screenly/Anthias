#!/usr/bin/python

from jinja2 import Template
from netifaces import gateways, interfaces
from os import getenv, path
import re
import sh

from lib.utils import generate_perfect_paper_password


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
    r = re.compile("wlan*")

    if not gateways().get('default') and filter(r.match, interfaces()):
        ssid = 'ScreenlyOSE-{}'.format(generate_perfect_paper_password(pw_length=4, has_symbols=False))
        ssid_password = generate_perfect_paper_password(pw_length=8, has_symbols=False)
        generate_page(ssid, ssid_password, 'screenly.io/wifi')

        wifi_connect = sh.sudo('wifi-connect', '-s', ssid, '-p', ssid_password, '-o', '9090')
    else:
        pass
