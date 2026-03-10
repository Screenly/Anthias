from django.shortcuts import render

from anthias_app.models import Asset
from lib.auth import authorized
from settings import settings


def _get_asset_context():
    assets = Asset.objects.all().order_by('play_order')
    active = [a for a in assets if a.is_active()]
    inactive = [a for a in assets if not a.is_active()]
    return {'active_assets': active, 'inactive_assets': inactive}


@authorized
def schedule(request):
    context = _get_asset_context()
    context['active_page'] = 'schedule'
    context['player_name'] = settings.get('player_name', '')
    context['default_duration'] = settings.get('default_duration', 10)
    return render(request, 'anthias_app/schedule.html', context)


@authorized
def asset_tables(request):
    context = _get_asset_context()
    return render(
        request, 'anthias_app/assets/tables.html', context
    )


@authorized
def settings_page(request):
    settings.load()
    context = {
        'active_page': 'settings',
        'settings': {
            'player_name': settings.get('player_name', ''),
            'audio_output': settings.get('audio_output', 'hdmi'),
            'default_duration': int(settings.get('default_duration', 10)),
            'default_streaming_duration': int(
                settings.get('default_streaming_duration', 300)
            ),
            'date_format': settings.get('date_format', 'mm/dd/yyyy'),
            'auth_backend': settings.get('auth_backend', ''),
            'show_splash': settings.get('show_splash', True),
            'default_assets': settings.get('default_assets', False),
            'shuffle_playlist': settings.get('shuffle_playlist', False),
            'use_24_hour_clock': settings.get('use_24_hour_clock', False),
            'debug_logging': settings.get('debug_logging', False),
            'username': (
                settings.get('user', '')
                if settings.get('auth_backend') == 'auth_basic'
                else ''
            ),
        },
    }
    return render(request, 'anthias_app/settings.html', context)


@authorized
def system_info(request):
    return render(
        request,
        'anthias_app/system_info.html',
        {'active_page': 'system_info'},
    )


@authorized
def system_info_data(request):
    from datetime import timedelta
    from os import statvfs
    from platform import machine

    import psutil
    from hurry.filesize import size

    from anthias_app.tasks import get_display_power_value
    from lib import device_helper, diagnostics
    from lib.github import is_up_to_date
    from lib.utils import get_node_ip, get_node_mac_address

    slash = statvfs('/')
    free_space = size(slash.f_bavail * slash.f_frsize)
    display_power = get_display_power_value()

    system_uptime = timedelta(seconds=diagnostics.get_uptime())
    virtual_memory = psutil.virtual_memory()
    device_model = device_helper.parse_cpu_info().get('model')
    if device_model is None and machine() == 'x86_64':
        device_model = 'Generic x86_64 Device'

    git_branch = diagnostics.get_git_branch()
    git_short_hash = diagnostics.get_git_short_hash()
    anthias_version = '{}@{}'.format(git_branch, git_short_hash)

    ip_addresses = []
    import ipaddress

    node_ip = get_node_ip()
    if node_ip != 'Unable to retrieve IP.':
        for ip in node_ip.split():
            ip_obj = ipaddress.ip_address(ip)
            if isinstance(ip_obj, ipaddress.IPv6Address):
                ip_addresses.append('http://[{}]'.format(ip))
            else:
                ip_addresses.append('http://{}'.format(ip))

    info = {
        'loadavg': diagnostics.get_load_avg()['15 min'],
        'free_space': free_space,
        'display_power': display_power,
        'up_to_date': is_up_to_date(),
        'anthias_version': anthias_version,
        'device_model': device_model,
        'uptime': {
            'days': system_uptime.days,
            'hours': round(system_uptime.seconds / 3600, 2),
        },
        'memory': {
            'total': virtual_memory.total >> 20,
            'used': virtual_memory.used >> 20,
            'free': virtual_memory.free >> 20,
            'available': virtual_memory.available >> 20,
        },
        'ip_addresses': ip_addresses,
        'mac_address': get_node_mac_address(),
    }

    return render(
        request,
        'anthias_app/partials/system_info_data.html',
        {'info': info},
    )
