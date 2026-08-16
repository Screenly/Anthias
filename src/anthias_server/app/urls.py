from django.conf import settings as django_settings
from django.urls import path

from . import design_system, views

app_name = 'anthias_app'

# Every route declares a trailing slash so Django's APPEND_SLASH=True
# (default) handles the slashless variant for free: `/system-info`
# permanently redirects to `/system-info/` (HTTP 301 for GET, 308
# method-preserving for POST/PUT/etc.) instead of 404-ing. The earlier
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
        'settings/display/<str:state>/',
        views.settings_display_power,
        name='settings_display_power',
    ),
    path(
        'settings/migrate-to-screenly/',
        views.migrate_to_screenly,
        name='migrate_to_screenly',
    ),
    path(
        'settings/import/<str:provider>/',
        views.import_content,
        name='import_content',
    ),
    path(
        '_partials/asset-table/',
        views.assets_table_partial,
        name='assets_table',
    ),
    path(
        'review-cta/dismiss/',
        views.review_cta_dismiss,
        name='review_cta_dismiss',
    ),
    path(
        'review-cta/snooze/',
        views.review_cta_snooze,
        name='review_cta_snooze',
    ),
    path('assets/new/', views.assets_create, name='assets_create'),
    # Path deliberately avoids the `assets/new` prefix: a test/selector
    # matching form[action*="assets/new"] would otherwise also match
    # this form's submit button.
    path('apps/install/', views.assets_create_app, name='assets_create_app'),
    path('assets/upload/', views.assets_upload, name='assets_upload'),
    path('assets/order/', views.assets_order, name='assets_order'),
    path(
        'assets/bulk/action/',
        views.assets_bulk_action,
        name='assets_bulk_action',
    ),
    path(
        'assets/bulk/update/',
        views.assets_bulk_update,
        name='assets_bulk_update',
    ),
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

# Dev-only: the design-system demo page, rendering every token and every
# component variant on one page.
#
# Gated at registration rather than inside the view, so on a production
# image (ENVIRONMENT=production) the route does not exist at all and
# there is no view to reach.
#
# The IS_TEST half is load-bearing, not belt-and-braces: pytest-django
# sets settings.DEBUG = False for the duration of a run, and this module
# is imported lazily on the first reverse() or request — i.e. from
# inside a test, after that assignment. Gating on DEBUG alone deletes
# the route exactly where tests/test_design_system_page.py needs it.
if django_settings.DEBUG or django_settings.IS_TEST:
    urlpatterns += [
        path(
            '_design/',
            design_system.design_system,
            name='design_system',
        ),
    ]
