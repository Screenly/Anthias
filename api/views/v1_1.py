import json

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from anthias_app.models import Asset
from api.helpers import (
    AssetCreationException,
)
from api.serializers import (
    AssetSerializer,
    CreateAssetSerializerV1_1,
    UpdateAssetSerializer,
)
from api.views.v1 import V1_ASSET_REQUEST
from lib.auth import authorized
from os import remove
from settings import settings


def parse_request(request):
    data = None

    # For backward compatibility
    try:
        data = json.loads(request.data)
    except ValueError:
        data = json.loads(request.data['model'])
    except TypeError:
        data = json.loads(request.data['model'])

    return data


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
        data = parse_request(request)

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
        asset = Asset.objects.get(asset_id=asset_id)
        return Response(AssetSerializer(asset).data)

    @extend_schema(
        summary='Update asset',
        request=V1_ASSET_REQUEST,
        responses={
            200: AssetSerializer
        }
    )
    @authorized
    def put(self, request, asset_id):
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
    def delete(self, request, asset_id):
        asset = Asset.objects.get(asset_id=asset_id)

        try:
            if asset.uri.startswith(settings['assetdir']):
                remove(asset.uri)
        except OSError:
            pass

        asset.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
