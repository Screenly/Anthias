import uuid
from base64 import b64encode
from inspect import cleandoc
from mimetypes import guess_extension, guess_type
from os import path, remove

from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from anthias_app.models import Asset
from api.helpers import save_active_assets_ordering
from celery_tasks import reboot_anthias, shutdown_anthias
from lib import backup_helper
from lib.auth import authorized
from settings import ZmqPublisher, settings


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
        responses={
            201: {
                'type': 'string',
                'example': 'anthias-backup-2021-09-16T15-00-00.tar.gz',
                'description': 'Backup file name'
            }
        }
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
                    'backup_upload': {
                        'type': 'string',
                        'format': 'binary'
                    }
                }
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
        file_upload = (request.data.get('backup_upload'))
        filename = file_upload.name

        if guess_type(filename)[0] != 'application/x-tar':
            raise Exception("Incorrect file extension.")
        try:
            publisher.send_to_viewer('stop')
            location = path.join("static", filename)

            with open(location, 'wb') as f:
                f.write(file_upload.read())

            backup_helper.recover(location)

            return Response("Recovery successful.")
        finally:
            publisher.send_to_viewer('play')


class RebootViewMixin(APIView):
    @extend_schema(summary='Reboot system')
    @authorized
    def post(self, request):
        reboot_anthias.apply_async()
        return Response(status=status.HTTP_200_OK)


class ShutdownViewMixin(APIView):
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
                    'file_upload': {
                        'type': 'string',
                        'format': 'binary'
                    }
                }
            }
        },
        responses={
            200: {
                'type': 'object',
                'properties': {
                    'uri': {'type': 'string'},
                    'ext': {'type': 'string'}
                }
            }
        }
    )
    @authorized
    def post(self, request):
        file_upload = request.data.get('file_upload')
        filename = file_upload.name
        file_type = guess_type(filename)[0]

        if not file_type:
            raise Exception("Invalid file type.")

        if file_type.split('/')[0] not in ['image', 'video']:
            raise Exception("Invalid file type.")

        file_path = path.join(
            settings['assetdir'],
            uuid.uuid5(uuid.NAMESPACE_URL, filename).hex,
        ) + ".tmp"

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
                }
            }
        }
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
                'mimetype': mimetype
            }
        else:
            result = {
                'type': 'url',
                'url': asset.uri
            }

        return Response(result)


class PlaylistOrderViewMixin(APIView):
    @extend_schema(
        summary='Update playlist order',
        request={
            'application/x-www-form-urlencoded': {
                'type': 'object',
                'properties': {
                    'ids': {
                        'type': 'string',
                        'description': cleandoc(
                            """
                            Comma-separated list of asset IDs in the order
                            they should be played. For example:

                            `793406aa1fd34b85aa82614004c0e63a,1c5cfa719d1f4a9abae16c983a18903b,9c41068f3b7e452baf4dc3f9b7906595`
                            """
                        )
                    }
                },
            }
        }
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
        ]
    )
    @authorized
    def get(self, request, command):
        publisher = ZmqPublisher.get_instance()
        publisher.send_to_viewer(command)
        return Response("Asset switched")
