from django.urls import path
from .views.v1 import (
    AssetViewV1,
    AssetListViewV1,
    AssetContentViewV1,
    FileAssetViewV1,
    PlaylistOrderViewV1,
    BackupViewV1,
    RecoverViewV1,
    AssetsControlViewV1,
    InfoView,
    RebootViewV1,
    ShutdownViewV1,
    ViewerCurrentAssetViewV1
)
from .views.v1_1 import (
    AssetListViewV1_1,
    AssetViewV1_1
)
from .views.v1_2 import (
    AssetListViewV1_2,
    AssetViewV1_2
)
from .views.v2 import (
    AssetContentViewV2,
    AssetsControlViewV2,
    AssetListViewV2,
    AssetViewV2,
    BackupViewV2,
    PlaylistOrderViewV2,
    RecoverViewV2,
    RebootViewV2,
    ShutdownViewV2,
    FileAssetViewV2
)

app_name = 'api'

urlpatterns = [
    # v1 endpoints
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

    # v1.1 endpoints
    path('v1.1/assets', AssetListViewV1_1.as_view(), name='asset_list_v1_1'),
    path(
        'v1.1/assets/<str:asset_id>',
        AssetViewV1_1.as_view(),
        name='asset_detail_v1_1',
    ),

    # v1.2 endpoints
    path('v1.2/assets', AssetListViewV1_2.as_view(), name='asset_list_v1_2'),
    path(
        'v1.2/assets/<str:asset_id>',
        AssetViewV1_2.as_view(),
        name='asset_detail_v1_2',
    ),

    # v2 endpoints
    path('v2/assets', AssetListViewV2.as_view(), name='asset_list_v2'),
    path(
        'v2/assets/order',
        PlaylistOrderViewV2.as_view(),
        name='playlist_order_v2',
    ),
    path(
        'v2/assets/control/<str:command>',
        AssetsControlViewV2.as_view(),
        name='assets_control_v2',
    ),
    path(
        'v2/assets/<str:asset_id>',
        AssetViewV2.as_view(),
        name='asset_detail_v2'
    ),
    path('v2/backup', BackupViewV2.as_view(), name='backup_v2'),
    path('v2/recover', RecoverViewV2.as_view(), name='recover_v2'),
    path('v2/reboot', RebootViewV2.as_view(), name='reboot_v2'),
    path('v2/shutdown', ShutdownViewV2.as_view(), name='shutdown_v2'),
    path('v2/file_asset', FileAssetViewV2.as_view(), name='file_asset_v2'),
    path(
        'v2/assets/<str:asset_id>/content',
        AssetContentViewV2.as_view(),
        name='asset_content_v2',
    ),
]
