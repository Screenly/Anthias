import hashlib
import logging
from datetime import timedelta
from os import getenv, statvfs
from platform import machine

import psutil
from drf_spectacular.utils import extend_schema
from hurry.filesize import size
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from anthias_app.helpers import add_default_assets, remove_default_assets
from anthias_app.models import Asset
from api.helpers import (
    AssetCreationError,
    get_active_asset_ids,
    save_active_assets_ordering,
)
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
    get_node_mac_address,
    is_balena_app,
)
from settings import ZmqPublisher, settings

r = connect_to_redis()


class AssetListViewV2(APIView):
    serializer_class = AssetSerializerV2

    @extend_schema(
        summary='List assets',
        responses={
            200: AssetSerializerV2(many=True)
        }
    )
    @authorized
    def get(self, request):
        queryset = Asset.objects.all()
        serializer = AssetSerializerV2(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary='Create asset',
        request=CreateAssetSerializerV2,
        responses={
            201: AssetSerializerV2
        }
    )
    @authorized
    def post(self, request):
        try:
            serializer = CreateAssetSerializerV2(
                data=request.data, unique_name=True)

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
    def get(self, request, asset_id):
        asset = Asset.objects.get(asset_id=asset_id)
        serializer = self.serializer_class(asset)
        return Response(serializer.data)

    def update(self, request, asset_id, partial=False):
        asset = Asset.objects.get(asset_id=asset_id)
        serializer = UpdateAssetSerializerV2(
            asset, data=request.data, partial=partial)

        if serializer.is_valid():
            serializer.save()
        else:
            return Response(
                serializer.errors, status=status.HTTP_400_BAD_REQUEST)

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
        responses={
            200: AssetSerializerV2
        }
    )
    @authorized
    def patch(self, request, asset_id):
        return self.update(request, asset_id, partial=True)

    @extend_schema(
        summary='Update asset',
        request=UpdateAssetSerializerV2,
        responses={
            200: AssetSerializerV2
        }
    )
    @authorized
    def put(self, request, asset_id):
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
        responses={
            200: DeviceSettingsSerializerV2
        }
    )
    @authorized
    def get(self, request):
        try:
            # Force reload of settings
            settings.load()
        except Exception as e:
            logging.error(f'Failed to reload settings: {str(e)}')
            # Continue with existing settings if reload fails

        return Response({
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
                settings['user'] if settings['auth_backend'] == 'auth_basic'
                else ''
            ),
        })

    def update_auth_settings(self, data, auth_backend, current_pass_correct):
        if auth_backend == '':
            return

        if auth_backend != 'auth_basic':
            return

        new_user = data.get('username', '')
        new_pass = data.get('password', '').encode('utf-8')
        new_pass2 = data.get('password_2', '').encode('utf-8')
        new_pass = hashlib.sha256(new_pass).hexdigest() if new_pass else None
        new_pass2 = hashlib.sha256(new_pass2).hexdigest() if new_pass else None

        if settings['password']:
            if new_user != settings['user']:
                if current_pass_correct is None:
                    raise ValueError(
                        "Must supply current password to change username"
                    )
                if not current_pass_correct:
                    raise ValueError("Incorrect current password.")

                settings['user'] = new_user

            if new_pass:
                if current_pass_correct is None:
                    raise ValueError(
                        "Must supply current password to change password"
                    )
                if not current_pass_correct:
                    raise ValueError("Incorrect current password.")

                if new_pass2 != new_pass:
                    raise ValueError("New passwords do not match!")

                settings['password'] = new_pass

        else:
            if new_user:
                if new_pass and new_pass != new_pass2:
                    raise ValueError("New passwords do not match!")
                if not new_pass:
                    raise ValueError("Must provide password")
                settings['user'] = new_user
                settings['password'] = new_pass
            else:
                raise ValueError("Must provide username")

    @extend_schema(
        summary='Update device settings',
        request=UpdateDeviceSettingsSerializerV2,
        responses={
            200: {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string'}
                }
            },
            400: {
                'type': 'object',
                'properties': {'error': {'type': 'string'}}
            }
        }
    )
    @authorized
    def patch(self, request):
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
                        "Must supply current password to change "
                        "authentication method"
                    )
                if not settings.auth.check_password(current_password):
                    raise ValueError("Incorrect current password.")

            prev_auth_backend = settings['auth_backend']
            if not current_password and prev_auth_backend:
                current_pass_correct = None
            else:
                current_pass_correct = (
                    settings
                    .auth_backends[prev_auth_backend]
                    .check_password(current_password)
                )
            next_auth_backend = settings.auth_backends[auth_backend]

            self.update_auth_settings(
                data, next_auth_backend.name, current_pass_correct)
            settings['auth_backend'] = auth_backend

            # Update settings
            if 'player_name' in data:
                settings['player_name'] = data['player_name']
            if 'default_duration' in data:
                settings['default_duration'] = data['default_duration']
            if 'default_streaming_duration' in data:
                settings['default_streaming_duration'] = (
                    data['default_streaming_duration']
                )
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
            publisher = ZmqPublisher.get_instance()
            publisher.send_to_viewer('reload')

            return Response({'message': 'Settings were successfully saved.'})
        except Exception as e:
            return Response(
                {'error': f'An error occurred while saving settings: {e}'},
                status=400
            )


class InfoViewV2(InfoViewMixin):
    def get_anthias_version(self):
        git_branch = diagnostics.get_git_branch()
        git_short_hash = diagnostics.get_git_short_hash()

        return '{}@{}'.format(
            git_branch,
            git_short_hash,
        )

    def get_device_model(self):
        device_model = device_helper.parse_cpu_info().get('model')

        if device_model is None and machine() == 'x86_64':
            device_model = 'Generic x86_64 Device'

        return device_model

    def get_uptime(self):
        system_uptime = timedelta(seconds=diagnostics.get_uptime())
        return {
            'days': system_uptime.days,
            'hours': round(system_uptime.seconds / 3600, 2),
        }

    def get_memory(self):
        virtual_memory = psutil.virtual_memory()
        return {
            'total': virtual_memory.total >> 20,
            'used': virtual_memory.used >> 20,
            'free': virtual_memory.free >> 20,
            'shared': virtual_memory.shared >> 20,
            'buff': virtual_memory.buffers >> 20,
            'available': virtual_memory.available >> 20
        }

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
                            'hours': {'type': 'number'}
                        }
                    },
                    'memory': {
                        'type': 'object',
                        'properties': {
                            'total': {'type': 'integer'},
                            'used': {'type': 'integer'},
                            'free': {'type': 'integer'},
                            'shared': {'type': 'integer'},
                            'buff': {'type': 'integer'},
                            'available': {'type': 'integer'}
                        }
                    },
                    'mac_address': {'type': 'string'}
                }
            }
        }
    )
    @authorized
    def get(self, request):
        viewlog = "Not yet implemented"

        # Calculate disk space
        slash = statvfs("/")
        free_space = size(slash.f_bavail * slash.f_frsize)
        display_power = r.get('display_power')

        return Response({
            'viewlog': viewlog,
            'loadavg': diagnostics.get_load_avg()['15 min'],
            'free_space': free_space,
            'display_power': display_power,
            'up_to_date': is_up_to_date(),
            'anthias_version': self.get_anthias_version(),
            'device_model': self.get_device_model(),
            'uptime': self.get_uptime(),
            'memory': self.get_memory(),
            'mac_address': get_node_mac_address(),
        })


class IntegrationsViewV2(APIView):
    serializer_class = IntegrationsSerializerV2

    @extend_schema(
        summary='Get integrations information',
        responses={
            200: IntegrationsSerializerV2
        }
    )
    @authorized
    def get(self, request):
        data = {
            'is_balena': is_balena_app(),
        }

        if data['is_balena']:
            data.update({
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
            })

        serializer = self.serializer_class(data=data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)
