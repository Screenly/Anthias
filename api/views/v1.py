import uuid

from inspect import cleandoc
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView
from api.serializers.v1_1 import CreateAssetSerializerV1_1
from api.serializers import (
    AssetSerializer,
    UpdateAssetSerializer,
)
from api.helpers import (
    AssetCreationException,
    parse_request,
    save_active_assets_ordering,
)
from base64 import b64encode
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    extend_schema,
    inline_serializer,
    OpenApiExample,
    OpenApiParameter,
    OpenApiRequest,
)
from hurry.filesize import size
from lib import (
    backup_helper,
    diagnostics
)
from lib.auth import authorized
from lib.github import is_up_to_date
from lib.utils import connect_to_redis
from mimetypes import guess_type, guess_extension
from os import path, remove, statvfs
from anthias_app.models import Asset
from celery_tasks import reboot_anthias, shutdown_anthias
from settings import settings, ZmqCollector, ZmqPublisher


r = connect_to_redis()

MODEL_STRING_EXAMPLE = """
Yes, that is just a string of JSON not JSON itself it will be parsed on the
other end. It's recommended to set `Content-Type` to
`application/x-www-form-urlencoded` and send the model as a string.

```
model: "{
    "name": "Website",
    "mimetype": "webpage",
    "uri": "http://example.com",
    "is_active": 0,
    "start_date": "2017-02-02T00:33:00.000Z",
    "end_date": "2017-03-01T00:33:00.000Z",
    "duration": "10",
    "is_enabled": 0,
    "is_processing": 0,
    "nocache": 0,
    "play_order": 0,
    "skip_asset_check": 0
}"
```
"""

V1_ASSET_REQUEST = OpenApiRequest(
    inline_serializer(
        name='ModelString',
        fields={
            'model': serializers.CharField(
                help_text=MODEL_STRING_EXAMPLE,
            ),
        },
    ),
    examples=[
        OpenApiExample(
            name='Example 1',
            value={'model': MODEL_STRING_EXAMPLE}
        ),
    ],
)


class AssetViewV1(APIView):
    serializer_class = AssetSerializer

    @extend_schema(summary='Get asset')
    @authorized
    def get(self, request, asset_id, format=None):
        asset = Asset.objects.get(asset_id=asset_id)
        return Response(AssetSerializer(asset).data)

    @extend_schema(
        summary='Update asset',
        request=V1_ASSET_REQUEST,
        responses={
            201: AssetSerializer
        }
    )
    @authorized
    def put(self, request, asset_id, format=None):
        asset = Asset.objects.get(asset_id=asset_id)

        data = parse_request(request)
        serializer = UpdateAssetSerializer(asset, data=data, partial=False)

        if serializer.is_valid():
            serializer.save()
        else:
            return Response(
                serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        asset.refresh_from_db()
        return Response(AssetSerializer(asset).data)

    @extend_schema(summary='Delete asset')
    @authorized
    def delete(self, request, asset_id, format=None):
        asset = Asset.objects.get(asset_id=asset_id)

        try:
            if asset.uri.startswith(settings['assetdir']):
                remove(asset.uri)
        except OSError:
            pass

        asset.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AssetContentView(APIView):
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


class AssetListViewV1(APIView):
    serializer_class = AssetSerializer

    @extend_schema(
        summary='List assets',
        responses={
            200: AssetSerializer(many=True)
        }
    )
    @authorized
    def get(self, request, format=None):
        queryset = Asset.objects.all()
        serializer = AssetSerializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary='Create asset',
        request=V1_ASSET_REQUEST,
        responses={
            201: AssetSerializer
        }
    )
    @authorized
    def post(self, request, format=None):
        data = parse_request(request)

        try:
            serializer = CreateAssetSerializerV1_1(data=data)
            if not serializer.is_valid():
                raise AssetCreationException(serializer.errors)
        except AssetCreationException as error:
            return Response(error.errors, status=status.HTTP_400_BAD_REQUEST)

        asset = Asset.objects.create(**serializer.data)

        return Response(
            AssetSerializer(asset).data, status=status.HTTP_201_CREATED)


class FileAssetView(APIView):
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


class PlaylistOrderView(APIView):
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


class BackupView(APIView):
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


class RecoverView(APIView):
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


class AssetsControlView(APIView):
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


class InfoView(APIView):
    @extend_schema(
        summary='Get system information',
        responses={
            200: {
                'type': 'object',
                'properties': {
                    'viewlog': {'type': 'string'},
                    'loadavg': {'type': 'number'},
                    'free_space': {'type': 'string'},
                    'display_power': {'type': 'string'},
                    'up_to_date': {'type': 'boolean'}
                },
                'example': {
                    'viewlog': 'Not yet implemented',
                    'loadavg': 0.1,
                    'free_space': '10G',
                    'display_power': 'on',
                    'up_to_date': True
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
            'up_to_date': is_up_to_date()
        })


class RebootView(APIView):
    @extend_schema(summary='Reboot system')
    @authorized
    def post(self, request):
        reboot_anthias.apply_async()
        return Response(status=status.HTTP_200_OK)


class ShutdownView(APIView):
    @extend_schema(summary='Shut down system')
    @authorized
    def post(self, request):
        shutdown_anthias.apply_async()
        return Response(status=status.HTTP_200_OK)


class ViewerCurrentAssetView(APIView):
    @extend_schema(
        summary='Get current asset',
        description='Get the current asset being displayed on the screen',
        responses={200: AssetSerializer}
    )
    @authorized
    def get(self, request):
        collector = ZmqCollector.get_instance()

        publisher = ZmqPublisher.get_instance()
        publisher.send_to_viewer('current_asset_id')

        collector_result = collector.recv_json(2000)
        current_asset_id = collector_result.get('current_asset_id')

        if not current_asset_id:
            return Response([])

        queryset = Asset.objects.get(asset_id=current_asset_id)
        return Response(AssetSerializer(queryset).data)
