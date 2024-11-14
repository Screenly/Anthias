from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from anthias_app.models import Asset
from api.helpers import (
    AssetCreationException,
    get_active_asset_ids,
    save_active_assets_ordering,
)
from api.serializers.v2 import (
    AssetSerializerV2,
    CreateAssetSerializerV2,
)
from lib.auth import authorized


class AssetListViewV2(APIView):
    serializer_class = AssetSerializerV2

    @extend_schema(
        summary='List assets',
        responses={
            200: AssetSerializerV2(many=True)
        }
    )
    @authorized
    def get(self, request):
        queryset = Asset.objects.all()
        serializer = AssetSerializerV2(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary='Create asset',
        request=CreateAssetSerializerV2,
        responses={
            201: AssetSerializerV2
        }
    )
    @authorized
    def post(self, request):
        try:
            serializer = CreateAssetSerializerV2(
                data=request.data, unique_name=True)

            if not serializer.is_valid():
                raise AssetCreationException(serializer.errors)
        except AssetCreationException as error:
            return Response(error.errors, status=status.HTTP_400_BAD_REQUEST)

        active_asset_ids = get_active_asset_ids()
        asset = Asset.objects.create(**serializer.data)

        if asset.is_active():
            active_asset_ids.insert(asset.play_order, asset.asset_id)

        save_active_assets_ordering(active_asset_ids)
        asset.refresh_from_db()

        return Response(
            AssetSerializerV2(asset).data,
            status=status.HTTP_201_CREATED,
        )


class AssetViewV2(APIView):
    serializer_class = AssetSerializerV2

    @extend_schema(summary='Get asset')
    @authorized
    def get(self, request, asset_id):
        asset = Asset.objects.get(asset_id=asset_id)
        serializer = self.serializer_class(asset)
        return Response(serializer.data)
