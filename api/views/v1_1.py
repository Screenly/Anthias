from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.helpers import prepare_asset
from lib import assets_helper, db
from lib.utils import url_fails
from os import remove
from settings import settings


# @TODO: Use the following decorators: authorized, swagger
class AssetListViewV1_1(APIView):
    def get(self, request):
        with db.conn(settings['database']) as conn:
            assets = assets_helper.read(conn)
            return Response(assets)

    def post(self, request):
        asset = prepare_asset(request, unique_name=True)
        if url_fails(asset['uri']):
            raise Exception("Could not retrieve file. Check the asset URL.")
        with db.conn(settings['database']) as conn:
            result = assets_helper.create(conn, asset)
            return Response(result, status=status.HTTP_201_CREATED)


# @TODO: Use the following decorators: api_response, authorized, swagger
class AssetViewV1_1(APIView):
    def get(self, request, asset_id):
        with db.conn(settings['database']) as conn:
            result = assets_helper.read(conn, asset_id)
            return Response(result)

    def put(self, request, asset_id):
        with db.conn(settings['database']) as conn:
            result = assets_helper.update(conn, asset_id, prepare_asset(request))
            return Response(result)

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
