import logging
from typing import Any

from asgiref.sync import async_to_sync
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

WS_GROUP = 'ws_server'


class AssetConsumer(AsyncWebsocketConsumer):
    async def connect(self) -> None:
        await self.channel_layer.group_add(WS_GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, code: int) -> None:
        await self.channel_layer.group_discard(WS_GROUP, self.channel_name)

    async def asset_update(self, event: dict[str, Any]) -> None:
        # Plain text frame: the client only needs to know "something
        # changed" to fire htmx refresh-assets; carrying the full
        # changeset over WS would duplicate the partial render path.
        asset_id = event.get('asset_id', '')
        try:
            await self.send(text_data=asset_id)
        except RuntimeError as exc:
            # The browser can disconnect in the window between the
            # group_send dispatch and this send, so the ASGI server has
            # already emitted 'websocket.close' and channels raises
            # "Unexpected ASGI message 'websocket.send', after sending
            # 'websocket.close'" (Sentry ANTHIAS-1K). Match on that
            # message so a genuine send() failure (a serialization error,
            # a Channels bug) still propagates instead of being hidden.
            # group_discard runs in disconnect(), so this stale channel
            # is on its way out — drop the nudge; the client's 5s poll
            # keeps it consistent. Log at debug (with the asset_id) so the
            # race stays diagnosable without becoming a reportable event.
            if "Unexpected ASGI message 'websocket.send'" not in str(exc):
                raise
            logger.debug(
                'asset_update: send on a closed websocket for %r; client '
                'disconnected mid-broadcast',
                asset_id,
                exc_info=True,
            )


def notify_asset_update(asset_id: str = '*') -> None:
    """Fan-out a 'refresh' nudge to every connected browser.

    Sync wrapper around channels.layers.group_send so Django views
    and Celery tasks can fire it without going through asyncio. Pass
    the affected asset_id when known; '*' is a generic "table state
    changed" sentinel for write paths that touch many rows at once
    (reorder, settings save, ...).
    """
    layer = get_channel_layer()
    if layer is None:
        # No CHANNEL_LAYERS configured — quietly skip rather than
        # 500ing the request. The 5s poll still keeps the table
        # eventually-consistent.
        return
    try:
        async_to_sync(layer.group_send)(
            WS_GROUP, {'type': 'asset_update', 'asset_id': asset_id}
        )
    except Exception:
        # Redis hiccup / channel-layer outage — log and let the caller
        # carry on; the poll fallback covers correctness.
        logger.exception('notify_asset_update failed for %s', asset_id)
