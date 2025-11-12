from django.urls import path

from api.views.v1_1 import AssetListViewV1_1, AssetViewV1_1


def get_url_patterns():
    return [
        path(
            'v1.1/assets', AssetListViewV1_1.as_view(), name='asset_list_v1_1'
        ),
        path(
            'v1.1/assets/<str:asset_id>',
            AssetViewV1_1.as_view(),
            name='asset_detail_v1_1',
        ),
    ]
