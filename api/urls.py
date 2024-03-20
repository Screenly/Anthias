from django.urls import path
from .views import AssetListViewV1

app_name = 'api'

urlpatterns = [
    path('v1/assets/', AssetListViewV1.as_view(), name='asset_list_v1'),
]
