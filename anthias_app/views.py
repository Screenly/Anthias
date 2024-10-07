import ipaddress
import logging
import psutil
from datetime import timedelta
from flask import Blueprint, request
from hurry.filesize import size
from os import getenv, statvfs
from platform import machine
from urllib.parse import urlparse

from anthias_app.helpers import (
    add_default_assets,
    remove_default_assets,
    template,
)
from lib import (
    diagnostics,
    device_helper,
)
from lib.auth import authorized
from lib.utils import (
    connect_to_redis,
    get_balena_supervisor_version,
    get_node_ip,
    get_node_mac_address,
    is_balena_app,
    is_demo_node,
    is_docker,
)
from settings import (
    CONFIGURABLE_SETTINGS,
    DEFAULTS,
    settings,
    ZmqPublisher,
)

r = connect_to_redis()
anthias_app_bp = Blueprint('anthias_app', __name__)


@anthias_app_bp.route('/')
@authorized
def index():
    player_name = settings['player_name']
    my_ip = urlparse(request.host_url).hostname
    is_demo = is_demo_node()
    balena_uuid = getenv("BALENA_APP_UUID", None)

    ws_addresses = []

    if settings['use_ssl']:
        ws_addresses.append('wss://' + my_ip + '/ws/')
    else:
        ws_addresses.append('ws://' + my_ip + '/ws/')

    if balena_uuid:
        ws_addresses.append(
            'wss://{}.balena-devices.com/ws/'.format(balena_uuid))

    return template(
        'index.html',
        ws_addresses=ws_addresses,
        player_name=player_name,
        is_demo=is_demo,
        is_balena=is_balena_app(),
    )


@anthias_app_bp.route('/settings', methods=["GET", "POST"])
@authorized
def settings_page():
    context = {'flash': None}

    if request.method == "POST":
        try:
            # Put some request variables in local variables to make them
            # easier to read.
            current_pass = request.form.get('current-password', '')
            auth_backend = request.form.get('auth_backend', '')

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
            next_auth_backend.update_settings(current_pass_correct)
            settings['auth_backend'] = auth_backend

            for field, default in list(CONFIGURABLE_SETTINGS.items()):
                value = request.form.get(field, default)

                if not value and field in [
                    'default_duration',
                    'default_streaming_duration',
                ]:
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
    for field, default in list(DEFAULTS['viewer'].items()):
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
            )
        })

    try:
        ip_addresses = get_node_ip().split()
    except Exception as error:
        logging.warning(f"Error getting IP addresses: {error}")
        ip_addresses = ['IP_ADDRESS']

    context.update({
        'user': settings['user'],
        'need_current_password': bool(settings['auth_backend']),
        'is_balena': is_balena_app(),
        'is_docker': is_docker(),
        'auth_backend': settings['auth_backend'],
        'auth_backends': auth_backends,
        'ip_addresses': ip_addresses,
        'host_user': getenv('HOST_USER')
    })

    return template('settings.html', **context)


@anthias_app_bp.route('/system-info')
@authorized
def system_info():
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

    version = '{}@{}'.format(
        diagnostics.get_git_branch(),
        diagnostics.get_git_short_hash()
    )

    return template(
        'system-info.html',
        player_name=player_name,
        loadavg=loadavg,
        free_space=free_space,
        uptime=system_uptime,
        memory=memory,
        display_power=display_power,
        device_model=device_model,
        version=version,
        mac_address=get_node_mac_address(),
        is_balena=is_balena_app(),
    )


@anthias_app_bp.route('/integrations')
@authorized
def integrations():

    context = {
        'player_name': settings['player_name'],
        'is_balena': is_balena_app(),
    }

    if context['is_balena']:
        context['balena_device_id'] = getenv('BALENA_DEVICE_UUID')
        context['balena_app_id'] = getenv('BALENA_APP_ID')
        context['balena_app_name'] = getenv('BALENA_APP_NAME')
        context['balena_supervisor_version'] = get_balena_supervisor_version()
        context['balena_host_os_version'] = getenv('BALENA_HOST_OS_VERSION')
        context['balena_device_name_at_init'] = getenv(
            'BALENA_DEVICE_NAME_AT_INIT')

    return template('integrations.html', **context)


@anthias_app_bp.route('/splash-page')
def splash_page():
    ip_addresses = []

    for ip_address in get_node_ip().split():
        ip_address_object = ipaddress.ip_address(ip_address)

        if isinstance(ip_address_object, ipaddress.IPv6Address):
            ip_addresses.append(f'http://[{ip_address}]')
        else:
            ip_addresses.append(f'http://{ip_address}')

    return template('splash-page.html', ip_addresses=ip_addresses)
