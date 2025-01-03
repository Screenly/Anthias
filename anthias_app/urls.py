from django.urls import path

from . import views

app_name = 'anthias_app'

urlpatterns = [
    path('', views.index, name='index'),
    path('settings', views.settings_page, name='settings'),
    path('system-info', views.system_info, name='system_info'),
    path('integrations', views.integrations, name='integrations'),
    path('splash-page', views.splash_page, name='splash_page'),
]
