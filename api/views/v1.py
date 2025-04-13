from os import statvfs

from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiRequest,
    extend_schema,
    inline_serializer,
)
from hurry.filesize import size
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from anthias_app.models import Asset
from api.helpers import (
    AssetCreationError,
    parse_request,
)
from api.serializers import (
    AssetSerializer,
    UpdateAssetSerializer,
)
from api.serializers.v1_1 import CreateAssetSerializerV1_1
from api.views.mixins import (
    AssetContentViewMixin,
    AssetsControlViewMixin,
    BackupViewMixin,
    DeleteAssetViewMixin,
    FileAssetViewMixin,
    PlaylistOrderViewMixin,
    RebootViewMixin,
    RecoverViewMixin,
    ShutdownViewMixin,
)
from lib import diagnostics
from lib.auth import authorized
from lib.github import is_up_to_date
from lib.utils import connect_to_redis
from settings import ZmqCollector, ZmqPublisher

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


class AssetViewV1(APIView, DeleteAssetViewMixin):
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


class AssetContentViewV1(AssetContentViewMixin):
    pass


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
                raise AssetCreationError(serializer.errors)
        except AssetCreationError as error:
            return Response(error.errors, status=status.HTTP_400_BAD_REQUEST)

        asset = Asset.objects.create(**serializer.data)

        return Response(
            AssetSerializer(asset).data, status=status.HTTP_201_CREATED)


class FileAssetViewV1(FileAssetViewMixin):
    pass


class PlaylistOrderViewV1(PlaylistOrderViewMixin):
    pass


class BackupViewV1(BackupViewMixin):
    pass


class RecoverViewV1(RecoverViewMixin):
    pass


class AssetsControlViewV1(AssetsControlViewMixin):
    pass


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


class RebootViewV1(RebootViewMixin):
    pass


class ShutdownViewV1(ShutdownViewMixin):
    pass


class ViewerCurrentAssetViewV1(APIView):
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
