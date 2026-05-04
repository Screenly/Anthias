from django.urls import path

from . import views

app_name = 'anthias_app'

# Every route declares a trailing slash so Django's APPEND_SLASH=True
# (default) handles the slashless variant for free: `/system-info`
# 302-redirects to `/system-info/` instead of 404-ing. The earlier
# slashless convention worked for inbound `{% url %}` references but
# silently broke any external bookmark / share / copy-pasted link
# typed without the trailing slash. APPEND_SLASH only ADDS slashes,
# never removes them, so consistency-at-trailing-slash covers both
# directions.
urlpatterns = [
    path('splash-page/', views.splash_page, name='splash_page'),
    path('login/', views.login, name='login'),
    path('', views.home, name='home'),
    path('system-info/', views.system_info, name='system_info'),
    path('integrations/', views.integrations, name='integrations'),
    path('settings/', views.settings_view, name='settings'),
    path('settings/save/', views.settings_save, name='settings_save'),
    path('settings/backup/', views.settings_backup, name='settings_backup'),
    path('settings/recover/', views.settings_recover, name='settings_recover'),
    path('settings/reboot/', views.settings_reboot, name='settings_reboot'),
    path(
        'settings/shutdown/',
        views.settings_shutdown,
        name='settings_shutdown',
    ),
    path(
        '_partials/asset-table/',
        views.assets_table_partial,
        name='assets_table',
    ),
    path('assets/new/', views.assets_create, name='assets_create'),
    path('assets/upload/', views.assets_upload, name='assets_upload'),
    path('assets/order/', views.assets_order, name='assets_order'),
    path(
        'assets/control/<str:command>/',
        views.assets_control,
        name='assets_control',
    ),
    path(
        'assets/<str:asset_id>/update/',
        views.assets_update,
        name='assets_update',
    ),
    path(
        'assets/<str:asset_id>/toggle/',
        views.assets_toggle,
        name='assets_toggle',
    ),
    path(
        'assets/<str:asset_id>/delete/',
        views.assets_delete,
        name='assets_delete',
    ),
    path(
        'assets/<str:asset_id>/download/',
        views.assets_download,
        name='assets_download',
    ),
    path(
        'assets/<str:asset_id>/preview/',
        views.assets_preview,
        name='assets_preview',
    ),
]
