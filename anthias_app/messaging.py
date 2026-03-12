import logging

logger = logging.getLogger(__name__)


def send_to_viewer(
    command: str,
    data: str | None = None,
) -> None:
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer is None:
            logger.warning('No channel layer available')
            return

        msg = {'type': 'viewer.command', 'command': command}
        if data is not None:
            msg['data'] = data

        async_to_sync(channel_layer.group_send)('viewer', msg)
    except Exception:
        logger.exception('Failed to send to viewer')


def send_to_ui(data: dict) -> None:
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer is None:
            return

        async_to_sync(channel_layer.group_send)(
            'ui', {'type': 'ui.update', 'data': data}
        )
    except Exception:
        logger.exception('Failed to send to UI')
