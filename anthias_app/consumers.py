import json
import logging

from channels.generic.websocket import WebSocketConsumer

logger = logging.getLogger(__name__)


class ViewerConsumer(WebSocketConsumer):
    def connect(self):
        from channels.layers import get_channel_layer

        self.accept()
        channel_layer = get_channel_layer()
        if channel_layer is not None:
            from asgiref.sync import async_to_sync

            async_to_sync(channel_layer.group_add)(
                'viewer', self.channel_name
            )
        logger.info('Viewer WebSocket connected')

    def disconnect(self, close_code):
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer is not None:
            from asgiref.sync import async_to_sync

            async_to_sync(channel_layer.group_discard)(
                'viewer', self.channel_name
            )
        logger.info('Viewer WebSocket disconnected')

    def viewer_command(self, event):
        self.send(text_data=json.dumps({
            'command': event['command'],
            'data': event.get('data'),
        }))


class UIConsumer(WebSocketConsumer):
    def connect(self):
        from channels.layers import get_channel_layer

        self.accept()
        channel_layer = get_channel_layer()
        if channel_layer is not None:
            from asgiref.sync import async_to_sync

            async_to_sync(channel_layer.group_add)(
                'ui', self.channel_name
            )

    def disconnect(self, close_code):
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer is not None:
            from asgiref.sync import async_to_sync

            async_to_sync(channel_layer.group_discard)(
                'ui', self.channel_name
            )

    def ui_update(self, event):
        self.send(text_data=json.dumps(event.get('data', {})))
