from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.helpers import prepare_asset
from api.serializers import AssetSerializer
from api.views.v1 import V1_ASSET_REQUEST
from lib import assets_helper, db
from lib.utils import url_fails
from os import remove
from settings import settings


# @TODO: Use the following decorators: authorized
class AssetListViewV1_1(APIView):
    @extend_schema(
        summary='List assets',
        responses={
            200: AssetSerializer(many=True)
        }
    )
    def get(self, request):
        with db.conn(settings['database']) as conn:
            assets = assets_helper.read(conn)
            return Response(assets)

    @extend_schema(
        summary='Create asset',
        request=V1_ASSET_REQUEST,
        responses={
            201: AssetSerializer
        }
    )
    def post(self, request):
        asset = prepare_asset(request, unique_name=True)
        if url_fails(asset['uri']):
            raise Exception("Could not retrieve file. Check the asset URL.")
        with db.conn(settings['database']) as conn:
            result = assets_helper.create(conn, asset)
            return Response(result, status=status.HTTP_201_CREATED)


# @TODO: Use the following decorators: api_response, authorized
class AssetViewV1_1(APIView):
    @extend_schema(
        summary='Get asset',
        responses={
            200: AssetSerializer,
        }
    )
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
    def put(self, request, asset_id):
        with db.conn(settings['database']) as conn:
            result = assets_helper.update(conn, asset_id, prepare_asset(request))
            return Response(result)

    @extend_schema(summary='Delete asset')
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
