"""Page-context helpers for server-rendered Django views.

Each function returns the dict a corresponding template needs.
The DRF API views in api/views/v2.py call the same primitives
(diagnostics, device_helper, settings) so the JSON and HTML
surfaces stay in lockstep without going through the HTTP API.
"""

from datetime import timedelta
from os import getenv, statvfs
from platform import machine
from typing import Any

import psutil
from hurry.filesize import size

from anthias_common import device_helper
from anthias_common.utils import (
    connect_to_redis,
    get_node_mac_address,
    is_balena_app,
)
from anthias_server.lib import diagnostics
from anthias_server.lib.github import is_up_to_date
from anthias_server.settings import settings

_redis = connect_to_redis()


def navbar() -> dict[str, Any]:
    """Shared by every page; merged into context by helpers.template()."""
    return {
        'is_balena': is_balena_app(),
        'up_to_date': is_up_to_date(),
        'player_name': settings['player_name'],
    }


def system_info() -> dict[str, Any]:
    slash = statvfs('/')
    virtual_memory = psutil.virtual_memory()
    uptime = timedelta(seconds=diagnostics.get_uptime())
    device_model = device_helper.parse_cpu_info().get('model')
    if device_model is None and machine() == 'x86_64':
        device_model = 'Generic x86_64 Device'

    anthias_version = '{}@{}'.format(
        diagnostics.get_git_branch(),
        diagnostics.get_git_short_hash(),
    )

    return {
        'loadavg': diagnostics.get_load_avg()['15 min'],
        'free_space': size(slash.f_bavail * slash.f_frsize),
        'memory': {
            'total': virtual_memory.total >> 20,
            'used': virtual_memory.used >> 20,
            'free': virtual_memory.free >> 20,
            'shared': virtual_memory.shared >> 20,
            'buff': virtual_memory.buffers >> 20,
            'available': virtual_memory.available >> 20,
        },
        'uptime': {
            'days': uptime.days,
            'hours': round(uptime.seconds / 3600, 2),
        },
        'display_power': _redis.get('display_power'),
        'device_model': device_model,
        'anthias_version': anthias_version,
        'mac_address': get_node_mac_address(),
        'host_user': getenv('HOST_USER'),
    }


def integrations() -> dict[str, Any]:
    data: dict[str, Any] = {'is_balena': is_balena_app()}
    if data['is_balena']:
        data.update(
            {
                'balena_device_id': getenv('BALENA_DEVICE_UUID'),
                'balena_app_id': getenv('BALENA_APP_ID'),
                'balena_app_name': getenv('BALENA_APP_NAME'),
                'balena_supervisor_version': getenv(
                    'BALENA_SUPERVISOR_VERSION'
                ),
                'balena_host_os_version': getenv('BALENA_HOST_OS_VERSION'),
                'balena_device_name_at_init': getenv(
                    'BALENA_DEVICE_NAME_AT_INIT'
                ),
            }
        )
    return data
