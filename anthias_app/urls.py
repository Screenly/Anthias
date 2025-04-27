from django.urls import re_path

from . import views

app_name = 'anthias_app'

urlpatterns = [
    re_path(r'^(?!api/).*$', views.react, name='react'),
]
