from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from anthias_server.app import page_context
from anthias_server.lib.auth import authorized
from anthias_common.utils import (
    connect_to_redis,
)
from anthias_server.settings import settings

from .helpers import (
    template,
)

r = connect_to_redis()


_ANTHIAS_REPO_URL = 'https://github.com/Screenly/Anthias'


@authorized
def react(request: HttpRequest) -> HttpResponse:
    return template(request, 'react.html', {})


@authorized
@require_http_methods(['GET'])
def system_info(request: HttpRequest) -> HttpResponse:
    context = page_context.system_info()
    # Master-branch builds get a clickable link to the commit; other
    # branches stay as plain text (mirrors AnthiasVersionValue in the
    # old React component, which only built the link when branch==master).
    version = context.get('anthias_version') or ''
    branch, _, commit = version.partition('@')
    if branch == 'master' and commit:
        context['anthias_version_master_link'] = (
            f'{_ANTHIAS_REPO_URL}/commit/{commit}'
        )
    context['active_nav'] = 'system-info'
    return template(request, 'system_info.html', context)


@require_http_methods(['GET', 'POST'])
def login(request: HttpRequest) -> HttpResponse:
    if request.method == 'POST':
        username = request.POST.get('username') or ''
        password = request.POST.get('password') or ''

        auth = settings.auth
        if (
            auth is not None
            and hasattr(auth, '_check')
            and auth._check(username, password)
        ):
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
def splash_page(request: HttpRequest) -> HttpResponse:
    # IPs are populated client-side by polling /api/v2/network/ip-addresses
    # so the page renders immediately even when the host bus is slow on
    # first boot, and updates if IPs change during the splash's display
    # window (e.g. a DHCP renewal mid-splash). This also avoids the
    # historical `ipaddress.ip_address('Unknown')` crash that took the
    # whole render down on a flaky Balena supervisor.
    return template(
        request,
        'splash-page.html',
        {
            'splash_logo_url': settings['splash_logo_url'],
        },
    )
