from typing import Any

from channels.generic.websocket import AsyncWebsocketConsumer

class AllowedHostsOriginValidator:
    def __init__(self, application: Any) -> None: ...

class WebsocketDenier(AsyncWebsocketConsumer): ...
