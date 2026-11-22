from django.urls import path

from anthias_server.controller import views

app_name = 'controller'

urlpatterns = [
    path('', views.screens, name='screens'),
    path('_table/', views.screens_table, name='screens_table'),
    path('publish/', views.screens_publish, name='screens_publish'),
    path('command/', views.screens_command, name='screens_command'),
    path('refresh/', views.screens_refresh, name='screens_refresh'),
    path(
        'join/regenerate/',
        views.screens_regenerate,
        name='screens_regenerate',
    ),
    path(
        '<int:node_id>/remove/',
        views.screens_remove,
        name='screens_remove',
    ),
]
