from django.urls import path

from api.views.v2 import (
    AssetContentViewV2,
    AssetListViewV2,
    AssetsControlViewV2,
    AssetViewV2,
    BackupViewV2,
    DeviceSettingsViewV2,
    FileAssetViewV2,
    InfoViewV2,
    IntegrationsViewV2,
    PlaylistOrderViewV2,
    RebootViewV2,
    RecoverViewV2,
    ShutdownViewV2,
)


def get_url_patterns():
    return [
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
        path(
            'v2/device_settings',
            DeviceSettingsViewV2.as_view(),
            name='device_settings_v2',
        ),
        path(
            'v2/info',
            InfoViewV2.as_view(),
            name='info_v2',
        ),
        path(
            'v2/integrations',
            IntegrationsViewV2.as_view(),
            name='integrations_v2',
        ),
    ]
