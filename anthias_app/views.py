import ipaddress

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from lib.auth import authorized
from lib.utils import (
    connect_to_redis,
    get_node_ip,
)
from settings import settings

from .helpers import (
    template,
)

r = connect_to_redis()


@authorized
def react(request):
    return template(request, 'react.html', {})


@require_http_methods(['GET', 'POST'])
def login(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        if settings.auth._check(username, password):
            # Store credentials in session
            request.session['auth_username'] = username
            request.session['auth_password'] = password

            return redirect(reverse('anthias_app:react'))
        else:
            messages.error(request, 'Invalid username or password')
            return template(
                request, 'login.html', {'next': request.GET.get('next', '/')}
            )

    return template(
        request, 'login.html', {'next': request.GET.get('next', '/')}
    )


@require_http_methods(['GET'])
def splash_page(request):
    ip_addresses = []

    for ip_address in get_node_ip().split():
        ip_address_object = ipaddress.ip_address(ip_address)

        if isinstance(ip_address_object, ipaddress.IPv6Address):
            ip_addresses.append(f'http://[{ip_address}]')
        else:
            ip_addresses.append(f'http://{ip_address}')

    return template(
        request, 'splash-page.html', {'ip_addresses': ip_addresses}
    )
