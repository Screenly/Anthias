#!/usr/bin/python

from jinja2 import Template
from netifaces import gateways
from os import getenv, path
from pwgen import pwgen
import sh


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
    if not gateways().get('default'):
        ssid = 'ScreenlyOSE-{}'.format(pwgen(4, symbols=False))
        ssid_password = pwgen(8, symbols=False)
        generate_page(ssid, ssid_password, 'screenly.io/wifi')

        wifi_connect = sh.sudo('wifi-connect', '-s', ssid, '-p', ssid_password, '-o', '9090')
    else:
        pass
