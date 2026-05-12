from django.urls import URLPattern, URLResolver, path

from anthias_server.api.views.v1_2 import AssetListViewV1_2, AssetViewV1_2


def get_url_patterns() -> list[URLPattern | URLResolver]:
    return [
        path(
            'v1.2/assets', AssetListViewV1_2.as_view(), name='asset_list_v1_2'
        ),
        path(
            'v1.2/assets/<str:asset_id>',
            AssetViewV1_2.as_view(),
            name='asset_detail_v1_2',
        ),
    ]
