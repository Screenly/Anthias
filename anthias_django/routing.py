from django.urls import path

from anthias_app.consumers import UIConsumer, ViewerConsumer

websocket_urlpatterns = [
    path('ws/viewer/', ViewerConsumer.as_asgi()),
    path('ws/ui/', UIConsumer.as_asgi()),
]
