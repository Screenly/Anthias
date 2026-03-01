import logging
import sqlite3
import uuid
from base64 import b64encode
from inspect import cleandoc
from mimetypes import guess_extension, guess_type
from os import getenv, path, remove, statvfs

from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from hurry.filesize import size
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from anthias_app.models import Asset
from api.helpers import save_active_assets_ordering
from api.serializers.mixins import (
    BackupViewSerializerMixin,
    PlaylistOrderSerializerMixin,
    RebootViewSerializerMixin,
    ShutdownViewSerializerMixin,
)
from celery_tasks import reboot_anthias, shutdown_anthias
from lib import backup_helper, diagnostics
from lib.auth import authorized
from lib.github import is_up_to_date
from lib.utils import connect_to_redis
from settings import ZmqPublisher, settings

r = connect_to_redis()


class DeleteAssetViewMixin:
    @extend_schema(summary='Delete asset')
    @authorized
    def delete(self, request, asset_id):
        asset = Asset.objects.get(asset_id=asset_id)

        try:
            if asset.uri.startswith(settings['assetdir']):
                remove(asset.uri)
        except OSError:
            pass

        asset.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)


class BackupViewMixin(APIView):
    @extend_schema(
        summary='Create backup',
        description=cleandoc("""
        Create a backup of the current Anthias instance, which
        includes the following:
        * current settings
        * image and video assets
        * asset metadata (e.g. name, duration, play order, status),
          which is stored in a SQLite database
        """),
        request=BackupViewSerializerMixin,
        responses={
            201: {
                'type': 'string',
                'example': 'anthias-backup-2021-09-16T15-00-00.tar.gz',
                'description': 'Backup file name',
            }
        },
    )
    @authorized
    def post(self, request):
        filename = backup_helper.create_backup(name=settings['player_name'])
        return Response(filename, status=status.HTTP_201_CREATED)


class RecoverViewMixin(APIView):
    @extend_schema(
        summary='Recover from backup',
        description=cleandoc("""
        Recover data from a backup file. The backup file must be
        a `.tar.gz` file.
        """),
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {
                    'backup_upload': {'type': 'string', 'format': 'binary'}
                },
            }
        },
        responses={
            200: {
                'type': 'string',
                'example': 'Recovery successful.',
            }
        },
    )
    @authorized
    def post(self, request):
        publisher = ZmqPublisher.get_instance()
        file_upload = request.data.get('backup_upload')
        filename = file_upload.name

        if guess_type(filename)[0] != 'application/x-tar':
            raise Exception('Incorrect file extension.')
        try:
            publisher.send_to_viewer('stop')
            location = path.join('static', filename)

            with open(location, 'wb') as f:
                f.write(file_upload.read())

            backup_helper.recover(location)

            return Response('Recovery successful.')
        finally:
            publisher.send_to_viewer('play')


class RebootViewMixin(APIView):
    serializer_class = RebootViewSerializerMixin

    @extend_schema(summary='Reboot system')
    @authorized
    def post(self, request):
        reboot_anthias.apply_async()
        return Response(status=status.HTTP_200_OK)


class ShutdownViewMixin(APIView):
    serializer_class = ShutdownViewSerializerMixin

    @extend_schema(summary='Shut down system')
    @authorized
    def post(self, request):
        shutdown_anthias.apply_async()
        return Response(status=status.HTTP_200_OK)


class FileAssetViewMixin(APIView):
    @extend_schema(
        summary='Upload file asset',
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {
                    'file_upload': {'type': 'string', 'format': 'binary'}
                },
            }
        },
        responses={
            200: {
                'type': 'object',
                'properties': {
                    'uri': {'type': 'string'},
                    'ext': {'type': 'string'},
                },
            }
        },
    )
    @authorized
    def post(self, request):
        file_upload = request.data.get('file_upload')
        filename = file_upload.name
        file_type = guess_type(filename)[0]

        if not file_type:
            raise Exception('Invalid file type.')

        if file_type.split('/')[0] not in ['image', 'video']:
            raise Exception('Invalid file type.')

        file_path = (
            path.join(
                settings['assetdir'],
                uuid.uuid5(uuid.NAMESPACE_URL, filename).hex,
            )
            + '.tmp'
        )

        if 'Content-Range' in request.headers:
            range_str = request.headers['Content-Range']
            start_bytes = int(range_str.split(' ')[1].split('-')[0])
            with open(file_path, 'ab') as f:
                f.seek(start_bytes)
                f.write(file_upload.read())
        else:
            with open(file_path, 'wb') as f:
                f.write(file_upload.read())

        return Response({'uri': file_path, 'ext': guess_extension(file_type)})


class AssetContentViewMixin(APIView):
    @extend_schema(
        summary='Get asset content',
        description=cleandoc("""
        The content of the asset.
        `type` can either be `file` or `url`.

        In case of a file, the fields `mimetype`, `filename`, and `content`
        will be present. In case of a URL, the field `url` will be present.
        """),
        responses={
            200: {
                'type': 'object',
                'properties': {
                    'type': {'type': 'string'},
                    'url': {'type': 'string'},
                    'filename': {'type': 'string'},
                    'mimetype': {'type': 'string'},
                    'content': {'type': 'string'},
                },
            }
        },
    )
    @authorized
    def get(self, request, asset_id, format=None):
        asset = Asset.objects.get(asset_id=asset_id)

        if path.isfile(asset.uri):
            filename = asset.name

            with open(asset.uri, 'rb') as f:
                content = f.read()

            mimetype = guess_type(filename)[0]
            if not mimetype:
                mimetype = 'application/octet-stream'

            result = {
                'type': 'file',
                'filename': filename,
                'content': b64encode(content).decode(),
                'mimetype': mimetype,
            }
        else:
            result = {'type': 'url', 'url': asset.uri}

        return Response(result)


class PlaylistOrderViewMixin(APIView):
    @extend_schema(
        summary='Update playlist order',
        request=PlaylistOrderSerializerMixin,
        responses={204: None},
    )
    @authorized
    def post(self, request):
        asset_ids = request.data.get('ids', '').split(',')
        save_active_assets_ordering(asset_ids)

        return Response(status=status.HTTP_204_NO_CONTENT)


class AssetsControlViewMixin(APIView):
    @extend_schema(
        summary='Control asset playback',
        description=cleandoc("""
        Use any of the following commands to control asset playback:
        * `next` - Show the next asset
        * `previous` - Show the previous asset
        * `asset&{asset_id}` - Show the asset with the specified `asset_id`
        """),
        responses={
            200: {
                'type': 'string',
                'example': 'Asset switched',
            }
        },
        parameters=[
            OpenApiParameter(
                name='command',
                location=OpenApiParameter.PATH,
                type=OpenApiTypes.STR,
                enum=['next', 'previous', 'asset&{asset_id}'],
            )
        ],
    )
    @authorized
    def get(self, request, command):
        publisher = ZmqPublisher.get_instance()
        publisher.send_to_viewer(command)
        return Response('Asset switched')


class InfoViewMixin(APIView):
    @staticmethod
    def _get_viewlog():
        """Read the last played asset from viewlog.db.

        Returns a dict with the last entry or 'No data' if empty.
        """
        home = getenv('HOME', '')
        db_path = path.join(home, '.screenly', 'viewlog.db')

        if not path.exists(db_path):
            return 'No data'

        try:
            conn = sqlite3.connect(db_path, timeout=3)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT asset_id, asset_name, mimetype, started_at '
                'FROM viewlog ORDER BY id DESC LIMIT 1'
            ).fetchone()
            conn.close()
        except Exception as e:
            logging.warning('Failed to read viewlog: %s', e)
            return 'No data'

        if not row:
            return 'No data'

        return {
            'asset_id': row['asset_id'],
            'asset_name': row['asset_name'],
            'mimetype': row['mimetype'],
            'started_at': row['started_at'],
        }

    @extend_schema(
        summary='Get system information',
        responses={
            200: {
                'type': 'object',
                'properties': {
                    'viewlog': {
                        'oneOf': [
                            {
                                'type': 'object',
                                'properties': {
                                    'asset_id': {'type': 'string'},
                                    'asset_name': {'type': 'string'},
                                    'mimetype': {'type': 'string'},
                                    'started_at': {'type': 'string'},
                                },
                            },
                            {'type': 'string'},
                        ],
                    },
                    'loadavg': {'type': 'number'},
                    'free_space': {'type': 'string'},
                    'display_power': {'type': 'string'},
                    'up_to_date': {'type': 'boolean'},
                },
                'example': {
                    'viewlog': {
                        'asset_id': 'abc123',
                        'asset_name': 'My Video',
                        'mimetype': 'video',
                        'started_at': '2024-01-01T12:00:00+00:00',
                    },
                    'loadavg': 0.1,
                    'free_space': '10G',
                    'display_power': 'on',
                    'up_to_date': True,
                },
            }
        },
    )
    @authorized
    def get(self, request):
        viewlog = self._get_viewlog()

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
            }
        )
