from typing import Any

from channels.generic.websocket import AsyncWebsocketConsumer

WS_GROUP = 'ws_server'


class AssetConsumer(AsyncWebsocketConsumer):  # type: ignore[misc]
    async def connect(self) -> None:
        await self.channel_layer.group_add(WS_GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, code: int) -> None:
        await self.channel_layer.group_discard(WS_GROUP, self.channel_name)

    async def asset_update(self, event: dict[str, Any]) -> None:
        await self.send(text_data=event['asset_id'])
