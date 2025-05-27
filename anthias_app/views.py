import ipaddress

from django.views.decorators.http import require_http_methods

from lib.auth import authorized
from lib.utils import (
    connect_to_redis,
    get_node_ip,
)

from .helpers import (
    template,
)

r = connect_to_redis()


@authorized
def react(request):
    return template(request, 'react.html', {})


@require_http_methods(["GET"])
def splash_page(request):
    ip_addresses = []

    for ip_address in get_node_ip().split():
        ip_address_object = ipaddress.ip_address(ip_address)

        if isinstance(ip_address_object, ipaddress.IPv6Address):
            ip_addresses.append(f'http://[{ip_address}]')
        else:
            ip_addresses.append(f'http://{ip_address}')

    return template(request, 'splash-page.html', {
        'ip_addresses': ip_addresses
    })
