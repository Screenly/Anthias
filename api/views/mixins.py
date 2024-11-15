from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from lib.auth import authorized

from anthias_app.models import Asset
from os import remove
from settings import settings


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
