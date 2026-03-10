from django.urls import path

from . import views
from . import views_ui

app_name = 'anthias_app'

urlpatterns = [
    path('splash-page', views.splash_page, name='splash_page'),
    path('login/', views.login, name='login'),
    path('', views_ui.schedule, name='schedule'),
    path('settings/', views_ui.settings_page, name='settings'),
    path('system-info/', views_ui.system_info, name='system_info'),
    path(
        '_partials/system-info-data/',
        views_ui.system_info_data,
        name='system_info_data',
    ),
    path(
        '_partials/asset-tables/',
        views_ui.asset_tables,
        name='asset_tables',
    ),
]
