from django.urls import path, re_path

from . import views

app_name = 'anthias_app'

# Order: explicit paths first; the React catch-all stays last while pages
# get migrated one at a time. Cut a page over by pointing its name at the
# new view (e.g. `views.system_info`) instead of `views.react`.
urlpatterns = [
    path('splash-page', views.splash_page, name='splash_page'),
    path('login/', views.login, name='login'),
    path('', views.react, name='home'),
    path('system-info', views.system_info, name='system_info'),
    path('integrations', views.react, name='integrations'),
    path('settings', views.react, name='settings'),
    re_path(r'^(?!api/).*$', views.react, name='react'),
]
