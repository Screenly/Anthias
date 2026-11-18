from channels.security.websocket import WebsocketDenier
from django.urls import re_path

from anthias_server.app.consumers import AssetConsumer

websocket_urlpatterns = [
    re_path(r'^ws$', AssetConsumer.as_asgi()),
    # Catch-all, and it has to stay last — the routes are tried in
    # order and this one matches everything.
    #
    # Without it the outermost ``URLRouter`` raises ``ValueError: No
    # route found for path %r`` for any other path. Nothing catches
    # that: uvicorn logs it to ``uvicorn.error`` at ERROR and the
    # Sentry logging integration turns every one into an event. A
    # device reachable from the internet (or from a LAN with a
    # home-automation scanner on it) collects one per probe —
    # `rpc/Ws.GetStatus`, the Shelly Gen2 RPC endpoint, alone
    # accounted for the single largest error stream in the fleet
    # (ANTHIAS-62's sibling ANTHIAS-2D: 5.9k events, ~500/day, none
    # of them a fault on this device).
    #
    # ``WebsocketDenier`` is Channels' own refusal consumer — the one
    # ``AllowedHostsOriginValidator`` uses for a bad Origin. It closes
    # before accepting, which uvicorn turns into an HTTP 403 on the
    # handshake, so an unknown path is now answered rather than
    # crashed on.
    re_path(r'', WebsocketDenier.as_asgi()),
]
