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


_DATE_FORMAT_OPTIONS = (
    ('mm/dd/yyyy', 'month/day/year'),
    ('dd/mm/yyyy', 'day/month/year'),
    ('yyyy/mm/dd', 'year/month/day'),
    ('mm-dd-yyyy', 'month-day-year'),
    ('dd-mm-yyyy', 'day-month-year'),
    ('yyyy-mm-dd', 'year-month-day'),
    ('mm.dd.yyyy', 'month.day.year'),
    ('dd.mm.yyyy', 'day.month.year'),
    ('yyyy.mm.dd', 'year.month.day'),
)


def device_settings() -> dict[str, Any]:
    """Form values + dropdown choices for /settings.

    Pulls from the live settings object (no API hop). Adds the
    page-only state the React component used to track:
    `has_saved_basic_auth` (whether to show the Current Password
    field), `is_pi5` (whether to hide the 3.5mm jack option), and
    the choice tuples for the auth_backend / date_format dropdowns.
    """
    settings.load()
    # parse_cpu_info() returns Mapping[str, int | str] per its stub, so
    # cast to str before substring-checking against the Pi 5 model name —
    # mypy refuses `'X' in (int|str)` even though str-len-check works.
    device_model = str(device_helper.parse_cpu_info().get('model') or '')
    return {
        'player_name': settings['player_name'],
        'default_duration': settings['default_duration'],
        'default_streaming_duration': settings['default_streaming_duration'],
        'audio_output': settings['audio_output'],
        'date_format': settings['date_format'],
        'auth_backend': settings['auth_backend'],
        'username': settings['user'],
        'show_splash': settings['show_splash'],
        'default_assets': settings['default_assets'],
        'shuffle_playlist': settings['shuffle_playlist'],
        'use_24_hour_clock': settings['use_24_hour_clock'],
        'debug_logging': settings['debug_logging'],
        # Auth-form chrome
        'has_saved_basic_auth': bool(settings['auth_backend'] == 'auth_basic'),
        # Hide the 3.5mm jack option on Pi 5 — the jack moved off-board
        # on that revision (matches the React audio-output dropdown).
        'is_pi5': 'Raspberry Pi 5' in device_model,
        'date_format_options': _DATE_FORMAT_OPTIONS,
    }


def assets() -> dict[str, Any]:
    """Active + inactive asset lists for /.

    Partition matches what the operator can change directly from the
    home page: `is_enabled` (the Activity toggle in the row) AND
    NOT `is_processing` (transient upload-in-progress state). The
    stricter `Asset.is_active()` predicate also factors in the date
    range and the day-of-week / time-of-day window — that's what
    the scheduler/viewer use to decide what to play right now, but
    using it here would yank a row out of the Active section just
    because today's weekday isn't in the asset's play_days, and the
    operator would have no way to flip it back without editing the
    schedule. React's UI used the same operator-facing split.
    """
    from anthias_server.app.models import Asset

    qs = Asset.objects.all()
    active: list[Asset] = []
    inactive: list[Asset] = []
    for asset in qs:
        if asset.is_enabled and not asset.is_processing:
            active.append(asset)
        else:
            inactive.append(asset)
    active.sort(key=lambda a: a.play_order)
    inactive.sort(key=lambda a: a.play_order)
    return {
        'active_assets': active,
        'inactive_assets': inactive,
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
