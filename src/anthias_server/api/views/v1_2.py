from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from anthias_server.app.models import Asset
from anthias_common.youtube import dispatch_download
from anthias_server.api.helpers import (
    AssetCreationError,
    get_active_asset_ids,
    save_active_assets_ordering,
)
from anthias_server.api.serializers import (
    AssetSerializer,
    UpdateAssetSerializer,
)
from anthias_server.api.serializers.v1_2 import CreateAssetSerializerV1_2
from anthias_server.api.views.mixins import DeleteAssetViewMixin
from anthias_server.lib.auth import authorized


class AssetListViewV1_2(APIView):
    serializer_class = AssetSerializer

    @extend_schema(
        summary='List assets', responses={200: AssetSerializer(many=True)}
    )
    @authorized
    def get(self, request: Request) -> Response:
        queryset = Asset.objects.all()
        serializer = self.serializer_class(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary='Create asset',
        request=CreateAssetSerializerV1_2,
        responses={201: AssetSerializer},
    )
    @authorized
    def post(self, request: Request) -> Response:
        try:
            serializer = CreateAssetSerializerV1_2(
                data=request.data, unique_name=True
            )

            if not serializer.is_valid():
                raise AssetCreationError(serializer.errors)
        except AssetCreationError as error:
            return Response(error.errors, status=status.HTTP_400_BAD_REQUEST)

        active_asset_ids = get_active_asset_ids()
        asset = Asset.objects.create(**serializer.data)

        # Kick off the YouTube download out of band when the
        # serializer flagged a youtube_asset. Without this dispatch
        # a v1.2 youtube_asset create would leave the row stuck at
        # is_processing=1 forever — the prior serializer just
        # blocked the request on yt-dlp shellouts, so the dispatch
        # site didn't exist.
        if serializer._pending_youtube_uri:
            dispatch_download(asset.asset_id, serializer._pending_youtube_uri)

        if asset.is_active():
            active_asset_ids.insert(asset.play_order, asset.asset_id)

        save_active_assets_ordering(active_asset_ids)
        asset.refresh_from_db()

        return Response(
            AssetSerializer(asset).data,
            status=status.HTTP_201_CREATED,
        )


class AssetViewV1_2(APIView, DeleteAssetViewMixin):
    serializer_class = AssetSerializer

    @extend_schema(summary='Get asset')
    @authorized
    def get(self, request: Request, asset_id: str) -> Response:
        asset = Asset.objects.get(asset_id=asset_id)
        serializer = self.serializer_class(asset)
        return Response(serializer.data)

    def update(
        self,
        request: Request,
        asset_id: str,
        partial: bool = False,
    ) -> Response:
        asset = Asset.objects.get(asset_id=asset_id)
        serializer = UpdateAssetSerializer(
            asset, data=request.data, partial=partial
        )

        if serializer.is_valid():
            serializer.save()
        else:
            return Response(
                serializer.errors, status=status.HTTP_400_BAD_REQUEST
            )

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

        return Response(AssetSerializer(asset).data)

    @extend_schema(
        summary='Update asset',
        request=UpdateAssetSerializer,
        responses={200: AssetSerializer},
    )
    @authorized
    def patch(self, request: Request, asset_id: str) -> Response:
        return self.update(request, asset_id, partial=True)

    @extend_schema(
        summary='Update asset',
        request=UpdateAssetSerializer,
        responses={200: AssetSerializer},
    )
    @authorized
    def put(self, request: Request, asset_id: str) -> Response:
        return self.update(request, asset_id, partial=False)
