from inspect import cleandoc
from drf_spectacular.utils import extend_schema
from mimetypes import guess_type
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from lib import backup_helper
from lib.auth import authorized

from anthias_app.models import Asset
from celery_tasks import reboot_anthias, shutdown_anthias
from os import path, remove
from settings import settings, ZmqPublisher


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
