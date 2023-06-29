from django.shortcuts import render, HttpResponse
from os import getenv
from settings import settings
from urllib.parse import urlparse

import logging

from lib.utils import (
    is_balena_app,
    is_demo_node,
)


def index(request):
    player_name = settings['player_name']
    my_ip = urlparse(request.build_absolute_uri()).hostname
    is_demo = is_demo_node()
    resin_uuid = getenv("RESIN_UUID", None)

    ws_addresses = []

    if settings['use_ssl']:
        ws_addresses.append('wss://' + my_ip + '/ws/')
    else:
        ws_addresses.append('ws://' + my_ip + '/ws/')

    if resin_uuid:
        ws_addresses.append('wss://{}.resindevice.io/ws/'.format(resin_uuid))

    return render(request, 'index.html', dict(
        ws_addresses=ws_addresses,
        player_name=player_name,
        is_demo=is_demo,
        is_balena=is_balena_app(),
    ))
