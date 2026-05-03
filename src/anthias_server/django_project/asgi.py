import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'anthias_server.django_project.settings')

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import (  # noqa: E402
    AllowedHostsOriginValidator,
)

from anthias_server.django_project.routing import websocket_urlpatterns  # noqa: E402

# AllowedHostsOriginValidator gates WebSocket handshakes on the same
# ALLOWED_HOSTS list as Django's HTTP layer. With ALLOWED_HOSTS=['*']
# this is currently a no-op, but keeping the wrapper means tightening
# ALLOWED_HOSTS automatically tightens /ws as well.
application = ProtocolTypeRouter(
    {
        'http': django_asgi_app,
        'websocket': AllowedHostsOriginValidator(
            URLRouter(websocket_urlpatterns)
        ),
    }
)
