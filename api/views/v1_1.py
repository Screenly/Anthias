import json

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from anthias_app.models import Asset
from api.helpers import (
    AssetCreationException,
    prepare_asset,
)
from api.serializers import (
    AssetSerializer,
    CreateAssetSerializerV1_1,
)
from api.views.v1 import V1_ASSET_REQUEST
from lib import assets_helper, db
from lib.auth import authorized
from os import remove
from settings import settings


class AssetListViewV1_1(APIView):
    @extend_schema(
        summary='List assets',
        responses={
            200: AssetSerializer(many=True)
        }
    )
    @authorized
    def get(self, request):
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
    def post(self, request):
        data = None

        # For backward compatibility
        try:
            data = json.loads(request.data)
        except ValueError:
            data = json.loads(request.data['model'])
        except TypeError:
            data = json.loads(request.data['model'])

        try:
            serializer = CreateAssetSerializerV1_1(data=data, unique_name=True)
            if not serializer.is_valid():
                raise AssetCreationException(serializer.errors)
        except AssetCreationException as error:
            return Response(error.errors, status=status.HTTP_400_BAD_REQUEST)

        asset = Asset.objects.create(**serializer.data)

        return Response(
            AssetSerializer(asset).data, status=status.HTTP_201_CREATED)


class AssetViewV1_1(APIView):
    @extend_schema(
        summary='Get asset',
        responses={
            200: AssetSerializer,
        }
    )
    @authorized
    def get(self, request, asset_id):
        with db.conn(settings['database']) as conn:
            result = assets_helper.read(conn, asset_id)
            return Response(result)

    @extend_schema(
        summary='Update asset',
        request=V1_ASSET_REQUEST,
        responses={
            201: AssetSerializer
        }
    )
    @authorized
    def put(self, request, asset_id):
        with db.conn(settings['database']) as conn:
            result = assets_helper.update(
                conn, asset_id, prepare_asset(request))
            return Response(result)

    @extend_schema(summary='Delete asset')
    @authorized
    def delete(self, request, asset_id):
        with db.conn(settings['database']) as conn:
            asset = assets_helper.read(conn, asset_id)
            try:
                if asset['uri'].startswith(settings['assetdir']):
                    remove(asset['uri'])
            except OSError:
                pass
            assets_helper.delete(conn, asset_id)
            return Response(status=status.HTTP_204_NO_CONTENT)
