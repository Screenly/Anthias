import ipaddress

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from lib.utils import get_node_ip
from settings import settings

from .helpers import template


@require_http_methods(['GET', 'POST'])
def login(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        if settings.auth._check(username, password):
            request.session['auth_username'] = username
            request.session['auth_password'] = password

            return redirect(reverse('anthias_app:schedule'))
        else:
            messages.error(request, 'Invalid username or password')
            return template(
                request,
                'login.html',
                {'next': request.GET.get('next', '/')},
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
