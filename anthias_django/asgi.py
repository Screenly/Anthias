import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'anthias_django.settings')

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from anthias_django.routing import websocket_urlpatterns  # noqa: E402

# No explicit Origin validator: the device is reached on a LAN by IP and
# ALLOWED_HOSTS=['*']. If hosts are ever locked down, wrap the URLRouter
# in channels.security.websocket.AllowedHostsOriginValidator to apply the
# same allowlist to WebSocket handshakes.
application = ProtocolTypeRouter(
    {
        'http': django_asgi_app,
        'websocket': URLRouter(websocket_urlpatterns),
    }
)
