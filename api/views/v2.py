from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from anthias_app.models import Asset
from api.helpers import (
    AssetCreationError,
    get_active_asset_ids,
    save_active_assets_ordering,
)
from api.serializers.v2 import (
    AssetSerializerV2,
    CreateAssetSerializerV2,
    UpdateAssetSerializerV2,
)
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
                raise AssetCreationError(serializer.errors)
        except AssetCreationError as error:
            return Response(error.errors, status=status.HTTP_400_BAD_REQUEST)

        active_asset_ids = get_active_asset_ids()
        asset = Asset.objects.create(**serializer.data)
        asset.refresh_from_db()

        if asset.is_active():
            active_asset_ids.insert(asset.play_order, asset.asset_id)

        save_active_assets_ordering(active_asset_ids)
        asset.refresh_from_db()

        return Response(
            AssetSerializerV2(asset).data,
            status=status.HTTP_201_CREATED,
        )


class AssetViewV2(APIView, DeleteAssetViewMixin):
    serializer_class = AssetSerializerV2

    @extend_schema(summary='Get asset')
    @authorized
    def get(self, request, asset_id):
        asset = Asset.objects.get(asset_id=asset_id)
        serializer = self.serializer_class(asset)
        return Response(serializer.data)

    def update(self, request, asset_id, partial=False):
        asset = Asset.objects.get(asset_id=asset_id)
        serializer = UpdateAssetSerializerV2(
            asset, data=request.data, partial=partial)

        if serializer.is_valid():
            serializer.save()
        else:
            return Response(
                serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        active_asset_ids = get_active_asset_ids()

        asset.refresh_from_db()

        try:
            active_asset_ids.remove(asset.asset_id)
        except ValueError:
            pass

        if asset.is_active():
            active_asset_ids.insert(asset.play_order, asset.asset_id)

        save_active_assets_ordering(active_asset_ids)
        asset.refresh_from_db()

        return Response(AssetSerializerV2(asset).data)

    @extend_schema(
        summary='Update asset',
        request=UpdateAssetSerializerV2,
        responses={
            200: AssetSerializerV2
        }
    )
    @authorized
    def patch(self, request, asset_id):
        return self.update(request, asset_id, partial=True)

    @extend_schema(
        summary='Update asset',
        request=UpdateAssetSerializerV2,
        responses={
            200: AssetSerializerV2
        }
    )
    @authorized
    def put(self, request, asset_id):
        return self.update(request, asset_id, partial=False)


class BackupViewV2(BackupViewMixin):
    pass


class RecoverViewV2(RecoverViewMixin):
    pass


class RebootViewV2(RebootViewMixin):
    pass


class ShutdownViewV2(ShutdownViewMixin):
    pass


class FileAssetViewV2(FileAssetViewMixin):
    pass


class AssetContentViewV2(AssetContentViewMixin):
    pass


class PlaylistOrderViewV2(PlaylistOrderViewMixin):
    pass


class AssetsControlViewV2(AssetsControlViewMixin):
    pass
