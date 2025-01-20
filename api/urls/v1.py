from django.urls import path

from api.views.v1 import (
    AssetContentViewV1,
    AssetListViewV1,
    AssetsControlViewV1,
    AssetViewV1,
    BackupViewV1,
    FileAssetViewV1,
    InfoView,
    PlaylistOrderViewV1,
    RebootViewV1,
    RecoverViewV1,
    ShutdownViewV1,
    ViewerCurrentAssetViewV1,
)


def get_url_patterns():
    return [
        path('v1/assets', AssetListViewV1.as_view(), name='asset_list_v1'),
        path(
            'v1/assets/order',
            PlaylistOrderViewV1.as_view(),
            name='playlist_order_v1',
        ),
        path(
            'v1/assets/control/<str:command>',
            AssetsControlViewV1.as_view(),
            name='assets_control_v1',
        ),
        path(
            'v1/assets/<str:asset_id>',
            AssetViewV1.as_view(),
            name='asset_detail_v1',
        ),
        path(
            'v1/assets/<str:asset_id>/content',
            AssetContentViewV1.as_view(),
            name='asset_content_v1',
        ),
        path('v1/file_asset', FileAssetViewV1.as_view(), name='file_asset_v1'),
        path('v1/backup', BackupViewV1.as_view(), name='backup_v1'),
        path('v1/recover', RecoverViewV1.as_view(), name='recover_v1'),
        path('v1/info', InfoView.as_view(), name='info_v1'),
        path('v1/reboot', RebootViewV1.as_view(), name='reboot_v1'),
        path('v1/shutdown', ShutdownViewV1.as_view(), name='shutdown_v1'),
        path(
            'v1/viewer_current_asset',
            ViewerCurrentAssetViewV1.as_view(),
            name='viewer_current_asset_v1',
        ),
    ]
