import ipaddress
from datetime import timedelta
from os import (
    getenv,
    statvfs,
)
from platform import machine
from urllib.parse import urlparse

import psutil
from django.views.decorators.http import require_http_methods
from hurry.filesize import size

from lib import (
    device_helper,
    diagnostics,
)
from lib.auth import authorized
from lib.utils import (
    connect_to_redis,
    get_node_ip,
    get_node_mac_address,
    is_balena_app,
    is_demo_node,
    is_docker,
)
from settings import (
    CONFIGURABLE_SETTINGS,
    DEFAULTS,
    ZmqPublisher,
    settings,
)

from .helpers import (
    add_default_assets,
    remove_default_assets,
    template,
)

r = connect_to_redis()


@authorized
@require_http_methods(["GET"])
def index(request):
    player_name = settings['player_name']
    my_ip = urlparse(request.build_absolute_uri()).hostname
    is_demo = is_demo_node()
    balena_device_uuid = getenv("BALENA_DEVICE_UUID", None)

    ws_addresses = []

    if settings['use_ssl']:
        ws_addresses.append('wss://' + my_ip + '/ws/')
    else:
        ws_addresses.append('ws://' + my_ip + '/ws/')

    if balena_device_uuid:
        ws_addresses.append(
            'wss://{}.balena-devices.com/ws/'.format(balena_device_uuid)
        )

    return template(request, 'index.html', {
        'ws_addresses': ws_addresses,
        'player_name': player_name,
        'is_demo': is_demo,
        'is_balena': is_balena_app(),
    })


@authorized
@require_http_methods(["GET", "POST"])
def settings_page(request):
    context = {'flash': None}

    if request.method == 'POST':
        try:
            current_pass = request.POST.get('current-password', '')
            auth_backend = request.POST.get('auth_backend', '')

            if (
                auth_backend != settings['auth_backend']
                and settings['auth_backend']
            ):
                if not current_pass:
                    raise ValueError(
                        "Must supply current password to change "
                        "authentication method"
                    )
                if not settings.auth.check_password(current_pass):
                    raise ValueError("Incorrect current password.")

            prev_auth_backend = settings['auth_backend']
            if not current_pass and prev_auth_backend:
                current_pass_correct = None
            else:
                current_pass_correct = (
                    settings
                    .auth_backends[prev_auth_backend]
                    .check_password(current_pass)
                )
            next_auth_backend = settings.auth_backends[auth_backend]
            next_auth_backend.update_settings(request, current_pass_correct)
            settings['auth_backend'] = auth_backend

            for field, default in list(CONFIGURABLE_SETTINGS.items()):
                value = request.POST.get(field, default)

                if (
                    not value
                    and field in [
                        'default_duration',
                        'default_streaming_duration',
                    ]
                ):
                    value = str(0)
                if isinstance(default, bool):
                    value = value == 'on'

                if field == 'default_assets' and settings[field] != value:
                    if value:
                        add_default_assets()
                    else:
                        remove_default_assets()

                settings[field] = value

            settings.save()
            publisher = ZmqPublisher.get_instance()
            publisher.send_to_viewer('reload')
            context['flash'] = {
                'class': "success",
                'message': "Settings were successfully saved.",
            }
        except ValueError as e:
            context['flash'] = {'class': "danger", 'message': e}
        except IOError as e:
            context['flash'] = {'class': "danger", 'message': e}
        except OSError as e:
            context['flash'] = {'class': "danger", 'message': e}
    else:
        settings.load()
    for field, _default in list(DEFAULTS['viewer'].items()):
        context[field] = settings[field]

    auth_backends = []
    for backend in settings.auth_backends_list:
        if backend.template:
            html, ctx = backend.template
            context.update(ctx)
        else:
            html = None
        auth_backends.append({
            'name': backend.name,
            'text': backend.display_name,
            'template': html,
            'selected': (
                'selected'
                if settings['auth_backend'] == backend.name
                else ''
            ),
        })

    ip_addresses = get_node_ip().split()

    context.update({
        'user': settings['user'],
        'need_current_password': bool(settings['auth_backend']),
        'is_balena': is_balena_app(),
        'is_docker': is_docker(),
        'auth_backend': settings['auth_backend'],
        'auth_backends': auth_backends,
        'ip_addresses': ip_addresses,
        'host_user': getenv('HOST_USER'),
        'device_type': getenv('DEVICE_TYPE')
    })

    return template(request, 'settings.html', context)


@authorized
@require_http_methods(["GET"])
def system_info(request):
    loadavg = diagnostics.get_load_avg()['15 min']
    display_power = r.get('display_power')

    # Calculate disk space
    slash = statvfs("/")
    free_space = size(slash.f_bavail * slash.f_frsize)

    # Memory
    virtual_memory = psutil.virtual_memory()
    memory = {
        'total': virtual_memory.total >> 20,
        'used': virtual_memory.used >> 20,
        'free': virtual_memory.free >> 20,
        'shared': virtual_memory.shared >> 20,
        'buff': virtual_memory.buffers >> 20,
        'available': virtual_memory.available >> 20
    }

    # Get uptime
    system_uptime = timedelta(seconds=diagnostics.get_uptime())

    # Player name for title
    player_name = settings['player_name']

    device_model = device_helper.parse_cpu_info().get('model')

    if device_model is None and machine() == 'x86_64':
        device_model = 'Generic x86_64 Device'

    git_branch = diagnostics.get_git_branch()
    git_short_hash = diagnostics.get_git_short_hash()
    anthias_commit_link = None

    if git_branch == 'master':
        anthias_commit_link = (
            'https://github.com/Screenly/Anthias'
            f'/commit/{git_short_hash}'
        )

    anthias_version = '{}@{}'.format(
        git_branch,
        git_short_hash,
    )

    context = {
        'player_name': player_name,
        'loadavg': loadavg,
        'free_space': free_space,
        'uptime': {
            'days': system_uptime.days,
            'hours': round(system_uptime.seconds / 3600, 2),
        },
        'memory': memory,
        'display_power': display_power,
        'device_model': device_model,
        'anthias_version': anthias_version,
        'anthias_commit_link': anthias_commit_link,
        'mac_address': get_node_mac_address(),
        'is_balena': is_balena_app(),
    }

    return template(request, 'system-info.html', context)


@authorized
@require_http_methods(["GET"])
def integrations(request):
    context = {
        'player_name': settings['player_name'],
        'is_balena': is_balena_app(),
    }

    if context['is_balena']:
        context.update({
            'balena_device_id': getenv('BALENA_DEVICE_UUID'),
            'balena_app_id': getenv('BALENA_APP_ID'),
            'balena_app_name': getenv('BALENA_APP_NAME'),
            'balena_supervisor_version': getenv('BALENA_SUPERVISOR_VERSION'),
            'balena_host_os_version': getenv('BALENA_HOST_OS_VERSION'),
            'balena_device_name_at_init': getenv('BALENA_DEVICE_NAME_AT_INIT'),
        })

    return template(request, 'integrations.html', context)


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
