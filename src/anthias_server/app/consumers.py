import asyncio
import contextlib
import logging
from typing import Any

from asgiref.sync import async_to_sync
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.layers import get_channel_layer

from anthias_common import now_playing
from anthias_common.utils import connect_to_redis_async

logger = logging.getLogger(__name__)

WS_GROUP = 'ws_server'


class AssetConsumer(AsyncWebsocketConsumer):
    async def connect(self) -> None:
        await self.channel_layer.group_add(WS_GROUP, self.channel_name)
        await self.accept()
        # Per-connection rather than process-wide, so its lifetime is
        # exactly this socket's. Note that vendor.ts opens /ws on every
        # page, not just the schedule page, so this is one Redis
        # subscription per open tab.
        self._now_playing_task = asyncio.create_task(self._watch_now_playing())

    async def disconnect(self, code: int) -> None:
        task = getattr(self, '_now_playing_task', None)
        if task is not None:
            task.cancel()
            # Awaited, not just cancelled: an un-retrieved task exception
            # (say the cleanup below failing) reaches asyncio's handler
            # as an ERROR log, which the Sentry logging integration turns
            # into a reported event — the same noise asset_update's
            # RuntimeError handling exists to avoid.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.channel_layer.group_discard(WS_GROUP, self.channel_name)

    async def _watch_now_playing(self) -> None:
        """Nudge this browser the moment the viewer changes asset.

        The table's 5s poll already keeps the now-playing highlight
        correct; this only decides whether the operator sees it land
        with the picture or up to 5s later (#3177). So every failure
        here — no Redis, a dropped subscription, a closed socket —
        ends the task quietly and leaves the poll in charge.
        """
        client = None
        try:
            client = connect_to_redis_async()
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            await pubsub.subscribe(now_playing.NOW_PLAYING_CHANNEL)
            async for message in pubsub.listen():
                data = message.get('data')
                if not isinstance(data, str):
                    continue
                await self.send(text_data=data)
        except Exception:
            logger.debug(
                'now-playing subscription ended; this browser falls back '
                'to the 5s table poll',
                exc_info=True,
            )
        finally:
            # Enough on its own: the client owns the pool the
            # subscription's connection came from, and aclose()
            # disconnects in-use connections too. Suppressed because
            # this runs on the cancellation path, where a raise would
            # become the task's unretrieved result.
            if client is not None:
                with contextlib.suppress(Exception):
                    await client.aclose()

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
            # 'websocket.close' or response already completed." (Sentry
            # ANTHIAS-1K). Require both the out-of-order 'websocket.send'
            # and the close/completed clause so a genuine send() failure
            # (a serialization error, a Channels bug) — even one that
            # merely mentions websocket.send — still propagates instead
            # of being hidden. group_discard runs in disconnect(), so
            # this stale channel is on its way out — drop the nudge; the
            # client's 5s poll keeps it consistent. Log at debug (with
            # the asset_id) so the race stays diagnosable without
            # becoming a reportable event.
            message = str(exc)
            is_send_after_close = (
                "Unexpected ASGI message 'websocket.send'" in message
                and (
                    'websocket.close' in message
                    or 'response already completed' in message
                )
            )
            if not is_send_after_close:
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
