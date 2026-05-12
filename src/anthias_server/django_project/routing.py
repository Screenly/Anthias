from django.urls import re_path

from anthias_server.app.consumers import AssetConsumer

websocket_urlpatterns = [
    re_path(r'^ws$', AssetConsumer.as_asgi()),
]
