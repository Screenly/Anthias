from django.urls import path, re_path

from . import views

app_name = 'anthias_app'

urlpatterns = [
    path('splash-page', views.splash_page, name='splash_page'),
    path('login/', views.login, name='login'),
    re_path(r'^(?!api/).*$', views.react, name='react'),
]
