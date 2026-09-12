import os

from django.core.asgi import get_asgi_application

os.environ.setdefault(
    'DJANGO_SETTINGS_MODULE', 'anthias_server.django_project.settings'
)

django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import (
    AllowedHostsOriginValidator,
)

from anthias_server.django_project.routing import (
    websocket_urlpatterns,
)

# Two gates on the WebSocket handshake, outermost first:
#
# 1. AllowedHostsOriginValidator checks the Origin header against the
#    same ALLOWED_HOSTS list as Django's HTTP layer. With
#    ALLOWED_HOSTS=['*'] this is currently a no-op, but keeping the
#    wrapper means tightening ALLOWED_HOSTS automatically tightens /ws
#    as well. It stays outermost so a cross-origin handshake is refused
#    before we spend a session lookup on it.
#
# 2. AuthMiddlewareStack (cookie -> session -> user) resolves
#    scope['user'] from the session cookie the browser sends with the
#    handshake. It only populates the scope; AssetConsumer.connect()
#    is what actually refuses an unauthenticated socket, and only when
#    the operator has auth switched on. Mirroring @authorized's
#    feature flag there rather than here keeps the "auth_backend == ''
#    means the device is fully open" contract in one place.
application = ProtocolTypeRouter(
    {
        'http': django_asgi_app,
        'websocket': AllowedHostsOriginValidator(
            AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
        ),
    }
)
