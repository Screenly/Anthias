from datetime import timedelta
from django.shortcuts import render, HttpResponse
from hurry.filesize import size
from os import (
    getenv,
    statvfs,
)
from settings import (
    CONFIGURABLE_SETTINGS,
    DEFAULTS,
    settings,
    ZmqPublisher,
)
from urllib.parse import urlparse
from lib import (
    diagnostics,
    raspberry_pi_helper,
)
from lib.utils import (
    connect_to_redis,
    generate_perfect_paper_password,
    get_node_ip,
    get_node_mac_address,
    is_balena_app,
    is_demo_node,
    is_docker,
)
from .helpers import template
import psutil


r = connect_to_redis()


# @TODO: Turn this into a class-based view.
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

    return template(request, 'index.html', {
        'ws_addresses': ws_addresses,
        'player_name': player_name,
        'is_demo': is_demo,
        'is_balena': is_balena_app(),
    })


# @TODO: Turn this into a class-based view.
def settings_page(request):
    context = {'flash': None}

    if request.method == 'POST':
        try:
            current_pass = request.POST.get('current-password', '')
            auth_backend = request.POST.get('auth_backend', '')

            if auth_backend != settings['auth_backend'] and settings['auth_backend']:
                if not current_pass:
                    raise ValueError("Must supply current password to change authentication method")
                if not settings.auth.check_password(current_pass):
                    raise ValueError("Incorrect current password.")

            prev_auth_backend = settings['auth_backend']
            if not current_pass and prev_auth_backend:
                current_pass_correct = None
            else:
                current_pass_correct = settings.auth_backends[prev_auth_backend].check_password(current_pass)
            next_auth_backend = settings.auth_backends[auth_backend]
            next_auth_backend.update_settings(current_pass_correct)
            settings['auth_backend'] = auth_backend

            for field, default in list(CONFIGURABLE_SETTINGS.items()):
                value = request.POST.get(field, default)

                if not value and field in ['default_duration', 'default_streaming_duration']:
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
            context['flash'] = {'class': "success", 'message': "Settings were successfully saved."}
        except ValueError as e:
            context['flash'] = {'class': "danger", 'message': e}
        except IOError as e:
            context['flash'] = {'class': "danger", 'message': e}
        except OSError as e:
            context['flash'] = {'class': "danger", 'message': e}
    else:
        settings.load()

    for field, default in list(DEFAULTS['viewer'].items()):
        if field == 'usb_assets_key':
            if not settings[field]:
                settings[field] = generate_perfect_paper_password(20, False)
                settings.save()
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
            'selected': 'selected' if settings['auth_backend'] == backend.name else ''
        })

    context.update({
        'user': settings['user'],
        'need_current_password': bool(settings['auth_backend']),
        'is_balena': is_balena_app(),
        'is_docker': is_docker(),
        'auth_backend': settings['auth_backend'],
        'auth_backends': auth_backends
    })

    return template(request, 'settings.html', context)


# @TODO: Turn this into a class-based view.
def system_info(request):
    viewlog = ["Yet to be implemented"]
    loadavg = diagnostics.get_load_avg()['15 min']
    display_info = diagnostics.get_monitor_status()
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

    raspberry_pi_model = raspberry_pi_helper.parse_cpu_info().get('model', "Unknown")

    screenly_version = '{}@{}'.format(
        diagnostics.get_git_branch(),
        diagnostics.get_git_short_hash()
    )

    context = {
        'player_name': player_name,
        'viewlog': viewlog,
        'loadavg': loadavg,
        'free_space': free_space,
        'uptime': {
            'days': system_uptime.days,
            'hours': round(system_uptime.seconds / 3600, 2),
        },
        'memory': memory,
        'display_info': display_info,
        'display_power': display_power,
        'raspberry_pi_model': raspberry_pi_model,
        'screenly_version': screenly_version,
        'mac_address': get_node_mac_address(),
        'is_balena': is_balena_app(),
    }

    return template(request, 'system-info.html', context)


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


def splash_page(request):
    my_ip = get_node_ip().split()
    return template(request, 'splash-page.html', {'my_ip': my_ip})
