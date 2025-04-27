from django.urls import path, re_path # todo nico; remove path if not needed

from . import views

app_name = 'anthias_app'

urlpatterns = [
    path('splash-page', views.splash_page, name='splash_page'),
    re_path(r'^(?!api/).*$', views.react, name='react'),
]
