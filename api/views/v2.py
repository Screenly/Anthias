import ipaddress
import json
import logging
import os
from datetime import timedelta
from os import getenv, statvfs
from platform import machine
from typing import Any

import psutil
import requests
from drf_spectacular.utils import extend_schema
from hurry.filesize import size
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from anthias_app.helpers import add_default_assets, remove_default_assets
from anthias_app.models import Asset
from api.helpers import (
    AssetCreationError,
    get_active_asset_ids,
    save_active_assets_ordering,
)
from lib.auth import hash_password
from api.serializers.v2 import (
    AssetSerializerV2,
    CreateAssetSerializerV2,
    DeviceSettingsSerializerV2,
    IntegrationsSerializerV2,
    UpdateAssetSerializerV2,
    UpdateDeviceSettingsSerializerV2,
)
from api.views.mixins import (
    AssetContentViewMixin,
    AssetsControlViewMixin,
    BackupViewMixin,
    DeleteAssetViewMixin,
    FileAssetViewMixin,
    InfoViewMixin,
    PlaylistOrderViewMixin,
    RebootViewMixin,
    RecoverViewMixin,
    ShutdownViewMixin,
)
from lib import device_helper, diagnostics
from lib.auth import authorized
from lib.github import is_up_to_date
from lib.utils import (
    connect_to_redis,
    get_node_ip,
    get_node_mac_address,
    is_balena_app,
)
from settings import ViewerPublisher, settings

r = connect_to_redis()


# Bounded HTTP timeout for the Balena supervisor lookup below. Hit by
# every poll on Balena devices, so it must stay well under the JS
# poll cadence (2s) to keep request workers free. A real supervisor
# answers in well under this; the timeout is the worst-case bound.
_BALENA_SUPERVISOR_TIMEOUT_S = 1.5


def _resolve_node_ip() -> str:
    """Non-blocking IP-string resolver for the splash polling endpoint.

    ``lib.utils.get_node_ip()`` is the right primitive for one-shot
    server-rendered surfaces like ``/api/v2/info``: it publishes
    ``set_ip_addresses`` to host_agent and waits up to ~80s
    (60s host_agent_ready + 20s ip_addresses_ready) for the result.
    That's catastrophic from inside a 2-second polling endpoint —
    request workers would queue behind a single slow first call and
    the splash would never populate.

    This resolver is the fast-path version. On bare metal it reads
    the cached ``ip_addresses`` key directly (host_agent populates it
    on demand) and fires a fire-and-forget ``hostcmd`` publish on a
    cache miss so the next poll (~2s later) finds something to read.
    On Balena it calls the supervisor directly with a tight HTTP
    timeout, since the supervisor is the only source of truth there
    and there's no host_agent cache to fall back on.

    Returns the raw whitespace-separated IP string (or empty), shaped
    to match what ``get_node_ip()`` would have returned, so the
    formatter below can stay shared with ``InfoViewV2``.
    """
    if is_balena_app():
        try:
            response = requests.get(
                '{}/v1/device?apikey={}'.format(
                    os.getenv('BALENA_SUPERVISOR_ADDRESS'),
                    os.getenv('BALENA_SUPERVISOR_API_KEY'),
                ),
                headers={'Content-Type': 'application/json'},
                timeout=_BALENA_SUPERVISOR_TIMEOUT_S,
            )
        except requests.RequestException:
            return ''
        if not response.ok:
            return ''
        try:
            return str(response.json().get('ip_address') or '')
        except ValueError:
            return ''

    raw = r.get('ip_addresses')
    if raw:
        try:
            return ' '.join(json.loads(raw))
        except (ValueError, TypeError):
            return ''

    # Cache miss. Best-effort: ask host_agent to populate. The next
    # poll picks it up — we don't block waiting for completion.
    try:
        r.publish('hostcmd', 'set_ip_addresses')
    except Exception:
        pass
    return ''


def _format_ip_urls(node_ip: str) -> list[str]:
    """Format a whitespace-separated IP string into clickable URLs.

    Tolerates malformed input: callers can pass leftover sentinel
    strings (e.g. 'Unknown' from ``get_node_ip()`` on a slow Balena
    boot) and ``ipaddress.ip_address()`` raises on those. Skipping
    invalid tokens keeps a single garbage value from 500-ing the
    consuming view.
    """
    if node_ip in ('Unknown', 'Unable to retrieve IP.'):
        return []
    out: list[str] = []
    for ip in node_ip.split():
        try:
            obj = ipaddress.ip_address(ip)
        except ValueError:
            continue
        # NOSONAR (S5332): Anthias serves the admin UI on plain HTTP per
        # CLAUDE.md; TLS is opt-in via the Caddy sidecar. The splash
        # surfaces these URLs so the operator can click into the
        # device's LAN UI — they must match how the device actually
        # listens. Emitting https:// here would point at a port that
        # isn't bound on a default install.
        if isinstance(obj, ipaddress.IPv6Address):
            out.append(f'http://[{ip}]')  # NOSONAR
        else:
            out.append(f'http://{ip}')  # NOSONAR
    return out


def _safe_ip_addresses() -> list[str]:
    """Fast-path resolver+formatter for the splash polling endpoint."""
    return _format_ip_urls(_resolve_node_ip())


class NetworkIpAddressesViewV2(APIView):
    """Lightweight IP-list endpoint for the splash page to poll.

    Unauth'd because the splash page itself is unauth'd and the viewer
    isn't a credentialed client. The data here is already disclosed by
    /splash-page rendering — there's no new exposure.

    Narrow on purpose: only IPs, no diagnostics. /api/v2/info covers
    the "everything about the device" case but is auth'd and does
    heavier work (psutil, statvfs, version checks) that would compound
    on a 2-second poll. Don't bolt onto this; add a sibling endpoint
    if a different unauth'd value is ever needed.
    """

    @extend_schema(
        summary='Get device IP addresses (for splash poll)',
        responses={
            200: {
                'type': 'object',
                'properties': {
                    'ip_addresses': {
                        'type': 'array',
                        'items': {'type': 'string'},
                    },
                },
            },
        },
    )
    def get(self, request: Request) -> Response:
        return Response({'ip_addresses': _safe_ip_addresses()})


class AssetListViewV2(APIView):
    serializer_class = AssetSerializerV2

    @extend_schema(
        summary='List assets', responses={200: AssetSerializerV2(many=True)}
    )
    @authorized
    def get(self, request: Request) -> Response:
        queryset = Asset.objects.all()
        serializer = AssetSerializerV2(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary='Create asset',
        request=CreateAssetSerializerV2,
        responses={201: AssetSerializerV2},
    )
    @authorized
    def post(self, request: Request) -> Response:
        try:
            serializer = CreateAssetSerializerV2(
                data=request.data, unique_name=True
            )

            if not serializer.is_valid():
                raise AssetCreationError(serializer.errors)
        except AssetCreationError as error:
            return Response(error.errors, status=status.HTTP_400_BAD_REQUEST)

        active_asset_ids = get_active_asset_ids()
        asset = Asset.objects.create(**serializer.data)
        asset.refresh_from_db()

        if asset.is_active():
            active_asset_ids.insert(asset.play_order, asset.asset_id)

        save_active_assets_ordering(active_asset_ids)
        asset.refresh_from_db()

        return Response(
            AssetSerializerV2(asset).data,
            status=status.HTTP_201_CREATED,
        )


class AssetViewV2(APIView, DeleteAssetViewMixin):
    serializer_class = AssetSerializerV2

    @extend_schema(summary='Get asset')
    @authorized
    def get(self, request: Request, asset_id: str) -> Response:
        asset = Asset.objects.get(asset_id=asset_id)
        serializer = self.serializer_class(asset)
        return Response(serializer.data)

    def update(
        self,
        request: Request,
        asset_id: str,
        partial: bool = False,
    ) -> Response:
        asset = Asset.objects.get(asset_id=asset_id)
        serializer = UpdateAssetSerializerV2(
            asset, data=request.data, partial=partial
        )

        if serializer.is_valid():
            serializer.save()
        else:
            return Response(
                serializer.errors, status=status.HTTP_400_BAD_REQUEST
            )

        active_asset_ids = get_active_asset_ids()

        asset.refresh_from_db()

        try:
            active_asset_ids.remove(asset.asset_id)
        except ValueError:
            pass

        if asset.is_active():
            active_asset_ids.insert(asset.play_order, asset.asset_id)

        save_active_assets_ordering(active_asset_ids)
        asset.refresh_from_db()

        return Response(AssetSerializerV2(asset).data)

    @extend_schema(
        summary='Update asset',
        request=UpdateAssetSerializerV2,
        responses={200: AssetSerializerV2},
    )
    @authorized
    def patch(self, request: Request, asset_id: str) -> Response:
        return self.update(request, asset_id, partial=True)

    @extend_schema(
        summary='Update asset',
        request=UpdateAssetSerializerV2,
        responses={200: AssetSerializerV2},
    )
    @authorized
    def put(self, request: Request, asset_id: str) -> Response:
        return self.update(request, asset_id, partial=False)


class BackupViewV2(BackupViewMixin):
    pass


class RecoverViewV2(RecoverViewMixin):
    pass


class RebootViewV2(RebootViewMixin):
    pass


class ShutdownViewV2(ShutdownViewMixin):
    pass


class FileAssetViewV2(FileAssetViewMixin):
    pass


class AssetContentViewV2(AssetContentViewMixin):
    pass


class PlaylistOrderViewV2(PlaylistOrderViewMixin):
    pass


class AssetsControlViewV2(AssetsControlViewMixin):
    pass


class DeviceSettingsViewV2(APIView):
    @extend_schema(
        summary='Get device settings',
        responses={200: DeviceSettingsSerializerV2},
    )
    @authorized
    def get(self, request: Request) -> Response:
        try:
            # Force reload of settings
            settings.load()
        except Exception as e:
            logging.error(f'Failed to reload settings: {str(e)}')
            # Continue with existing settings if reload fails

        return Response(
            {
                'player_name': settings['player_name'],
                'audio_output': settings['audio_output'],
                'default_duration': int(settings['default_duration']),
                'default_streaming_duration': int(
                    settings['default_streaming_duration']
                ),
                'date_format': settings['date_format'],
                'auth_backend': settings['auth_backend'],
                'show_splash': settings['show_splash'],
                'default_assets': settings['default_assets'],
                'shuffle_playlist': settings['shuffle_playlist'],
                'use_24_hour_clock': settings['use_24_hour_clock'],
                'debug_logging': settings['debug_logging'],
                'username': (
                    settings['user']
                    if settings['auth_backend'] == 'auth_basic'
                    else ''
                ),
            }
        )

    def update_auth_settings(
        self,
        data: dict[str, Any],
        auth_backend: str,
        current_pass_correct: bool | None,
    ) -> None:
        if auth_backend == '':
            return

        if auth_backend != 'auth_basic':
            return

        new_user = data.get('username', '')
        new_pass = data.get('password', '')
        new_pass2 = data.get('password_2', '')

        if settings['password']:
            if new_user != settings['user']:
                if current_pass_correct is None:
                    raise ValueError(
                        'Must supply current password to change username'
                    )
                if not current_pass_correct:
                    raise ValueError('Incorrect current password.')

                settings['user'] = new_user

            if new_pass:
                if current_pass_correct is None:
                    raise ValueError(
                        'Must supply current password to change password'
                    )
                if not current_pass_correct:
                    raise ValueError('Incorrect current password.')

                if new_pass2 != new_pass:
                    raise ValueError('New passwords do not match!')

                settings['password'] = hash_password(new_pass)

        else:
            if new_user:
                if new_pass and new_pass != new_pass2:
                    raise ValueError('New passwords do not match!')
                if not new_pass:
                    raise ValueError('Must provide password')
                settings['user'] = new_user
                settings['password'] = hash_password(new_pass)
            else:
                raise ValueError('Must provide username')

    @extend_schema(
        summary='Update device settings',
        request=UpdateDeviceSettingsSerializerV2,
        responses={
            200: {
                'type': 'object',
                'properties': {'message': {'type': 'string'}},
            },
            400: {
                'type': 'object',
                'properties': {'error': {'type': 'string'}},
            },
        },
    )
    @authorized
    def patch(self, request: Request) -> Response:
        try:
            serializer = UpdateDeviceSettingsSerializerV2(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=400)

            data = serializer.validated_data
            settings.load()

            current_password = data.get('current_password', '')
            auth_backend = data.get('auth_backend', '')

            if (
                auth_backend != settings['auth_backend']
                and settings['auth_backend']
            ):
                if not current_password:
                    raise ValueError(
                        'Must supply current password to change '
                        'authentication method'
                    )
                if settings.auth is None or not settings.auth.check_password(
                    current_password
                ):
                    raise ValueError('Incorrect current password.')

            prev_auth_backend = settings['auth_backend']
            if not current_password and prev_auth_backend:
                current_pass_correct = None
            else:
                current_pass_correct = settings.auth_backends[
                    prev_auth_backend
                ].check_password(current_password)
            next_auth_backend = settings.auth_backends[auth_backend]

            self.update_auth_settings(
                data, next_auth_backend.name, current_pass_correct
            )
            settings['auth_backend'] = auth_backend

            # Update settings
            if 'player_name' in data:
                settings['player_name'] = data['player_name']
            if 'default_duration' in data:
                settings['default_duration'] = data['default_duration']
            if 'default_streaming_duration' in data:
                settings['default_streaming_duration'] = data[
                    'default_streaming_duration'
                ]
            if 'audio_output' in data:
                settings['audio_output'] = data['audio_output']
            if 'date_format' in data:
                settings['date_format'] = data['date_format']
            if 'show_splash' in data:
                settings['show_splash'] = data['show_splash']
            if 'default_assets' in data:
                if data['default_assets'] and not settings['default_assets']:
                    add_default_assets()
                elif not data['default_assets'] and settings['default_assets']:
                    remove_default_assets()
                settings['default_assets'] = data['default_assets']
            if 'shuffle_playlist' in data:
                settings['shuffle_playlist'] = data['shuffle_playlist']
            if 'use_24_hour_clock' in data:
                settings['use_24_hour_clock'] = data['use_24_hour_clock']
            if 'debug_logging' in data:
                settings['debug_logging'] = data['debug_logging']

            settings.save()
            publisher = ViewerPublisher.get_instance()
            publisher.send_to_viewer('reload')

            return Response({'message': 'Settings were successfully saved.'})
        except Exception as e:
            return Response(
                {'error': f'An error occurred while saving settings: {e}'},
                status=400,
            )


class InfoViewV2(InfoViewMixin):
    def get_anthias_version(self) -> str:
        git_branch = diagnostics.get_git_branch()
        git_short_hash = diagnostics.get_git_short_hash()

        return '{}@{}'.format(
            git_branch,
            git_short_hash,
        )

    def get_device_model(self) -> str | int | None:
        device_model = device_helper.parse_cpu_info().get('model')

        if device_model is None and machine() == 'x86_64':
            device_model = 'Generic x86_64 Device'

        return device_model

    def get_uptime(self) -> dict[str, int | float]:
        system_uptime = timedelta(seconds=diagnostics.get_uptime())
        return {
            'days': system_uptime.days,
            'hours': round(system_uptime.seconds / 3600, 2),
        }

    def get_memory(self) -> dict[str, int]:
        virtual_memory = psutil.virtual_memory()
        return {
            'total': virtual_memory.total >> 20,
            'used': virtual_memory.used >> 20,
            'free': virtual_memory.free >> 20,
            'shared': virtual_memory.shared >> 20,
            'buff': virtual_memory.buffers >> 20,
            'available': virtual_memory.available >> 20,
        }

    def get_ip_addresses(self) -> list[str]:
        # /api/v2/info is auth'd and not polled, so blocking on
        # get_node_ip()'s host-readiness loop is acceptable here —
        # the formatter still tolerates 'Unknown'/'Unable to retrieve
        # IP.' sentinels via _format_ip_urls. The polling endpoint
        # (NetworkIpAddressesViewV2) uses _safe_ip_addresses() instead,
        # which never blocks.
        return _format_ip_urls(get_node_ip())

    @extend_schema(
        summary='Get system information',
        responses={
            200: {
                'type': 'object',
                'properties': {
                    'viewlog': {'type': 'string'},
                    'loadavg': {'type': 'number'},
                    'free_space': {'type': 'string'},
                    'display_power': {'type': ['string', 'null']},
                    'up_to_date': {'type': 'boolean'},
                    'anthias_version': {'type': 'string'},
                    'device_model': {'type': 'string'},
                    'uptime': {
                        'type': 'object',
                        'properties': {
                            'days': {'type': 'integer'},
                            'hours': {'type': 'number'},
                        },
                    },
                    'memory': {
                        'type': 'object',
                        'properties': {
                            'total': {'type': 'integer'},
                            'used': {'type': 'integer'},
                            'free': {'type': 'integer'},
                            'shared': {'type': 'integer'},
                            'buff': {'type': 'integer'},
                            'available': {'type': 'integer'},
                        },
                    },
                    'ip_addresses': {
                        'type': 'array',
                        'items': {'type': 'string'},
                    },
                    'mac_address': {'type': 'string'},
                    'host_user': {'type': 'string'},
                },
            }
        },
    )
    @authorized
    def get(self, request: Request) -> Response:
        viewlog = 'Not yet implemented'

        # Calculate disk space
        slash = statvfs('/')
        free_space = size(slash.f_bavail * slash.f_frsize)
        display_power = r.get('display_power')

        return Response(
            {
                'viewlog': viewlog,
                'loadavg': diagnostics.get_load_avg()['15 min'],
                'free_space': free_space,
                'display_power': display_power,
                'up_to_date': is_up_to_date(),
                'anthias_version': self.get_anthias_version(),
                'device_model': self.get_device_model(),
                'uptime': self.get_uptime(),
                'memory': self.get_memory(),
                'ip_addresses': self.get_ip_addresses(),
                'mac_address': get_node_mac_address(),
                'host_user': getenv('HOST_USER'),
            }
        )


class IntegrationsViewV2(APIView):
    serializer_class = IntegrationsSerializerV2

    @extend_schema(
        summary='Get integrations information',
        responses={200: IntegrationsSerializerV2},
    )
    @authorized
    def get(self, request: Request) -> Response:
        data: dict[str, Any] = {
            'is_balena': is_balena_app(),
        }

        if data['is_balena']:
            data.update(
                {
                    'balena_device_id': getenv('BALENA_DEVICE_UUID'),
                    'balena_app_id': getenv('BALENA_APP_ID'),
                    'balena_app_name': getenv('BALENA_APP_NAME'),
                    'balena_supervisor_version': (
                        getenv('BALENA_SUPERVISOR_VERSION')
                    ),
                    'balena_host_os_version': (
                        getenv('BALENA_HOST_OS_VERSION')
                    ),
                    'balena_device_name_at_init': (
                        getenv('BALENA_DEVICE_NAME_AT_INIT')
                    ),
                }
            )

        serializer = self.serializer_class(data=data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)
