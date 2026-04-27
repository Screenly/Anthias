from channels.generic.websocket import AsyncWebsocketConsumer

WS_GROUP = 'ws_server'


class AssetConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add(WS_GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(WS_GROUP, self.channel_name)

    async def asset_update(self, event):
        await self.send(text_data=event['asset_id'])
