import asyncio
import contextlib
import logging
from typing import Any

from asgiref.sync import async_to_sync
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.layers import get_channel_layer

from anthias_common import now_playing
from anthias_common.utils import connect_to_redis_async
from anthias_common.warn_once import WarnOnce

logger = logging.getLogger(__name__)

WS_GROUP = 'ws_server'

#: This module's own latch, not now_playing's: a push failure here is
#: the server's, and reaching into that module's instance would file it
#: in the journal under the viewer-side module's logger name.
_warn = WarnOnce(logger)

#: How long each read on the subscription waits before looping. Only
#: PubSub.check_health can notice a wedged connection, and it runs when
#: parse_response is re-entered -- never during a blocking listen() --
#: so a short poll is what gives health_check_interval anything to do.
_SUBSCRIPTION_POLL_S = 1.0

#: The process's single now-playing subscriber, and how many sockets
#: rely on it. Process-wide rather than per socket: /ws has no auth
#: and vendor.ts opens it on every page, so per-socket would let
#: anything that can reach the device claim a Redis connection and a
#: task per socket it opens. Per *process*, so this is one subscriber
#: per device only while bin/start_server.sh runs uvicorn without
#: --workers; N workers would mean N group_sends per rotation, each
#: fanning out to the whole shared group.
_now_playing_watcher: 'asyncio.Task[None] | None' = None
_open_sockets = 0


def _acquire_now_playing_watcher() -> None:
    """Start the subscriber if this is the first socket to need it.

    Restarts a finished task too: the body ends on any Redis failure,
    so a server that outlives an outage retries on the next connect
    instead of staying poll-only until the container restarts.
    """
    global _now_playing_watcher, _open_sockets
    _open_sockets += 1
    task = _now_playing_watcher
    # cancelling() as well as done(): a task that has been asked to
    # stop but has not unwound yet is on its way out, and handing it
    # back to a browser that just arrived would leave that browser on
    # poll-only for good.
    if task is not None and not task.done() and not task.cancelling():
        return
    _now_playing_watcher = asyncio.create_task(_watch_now_playing())


def _release_now_playing_watcher() -> None:
    """Stop the subscriber once the last socket has gone.

    Cancelled, not awaited: a cancelled task is not an unretrieved
    exception, so this costs no asyncio ERROR log (and so no Sentry
    event), and the task's ``finally`` closes the client on the next
    pass. Stopping at zero also leaves nothing pending at shutdown.

    The reference is kept rather than dropped, because the event loop
    holds only a weak one and the task still has an ``await`` to run
    in its ``finally``.
    """
    global _open_sockets
    _open_sockets = max(0, _open_sockets - 1)
    if _open_sockets or _now_playing_watcher is None:
        return
    _now_playing_watcher.cancel()


async def _watch_now_playing() -> None:
    """Bridge the viewer's now-playing announcements onto WS_GROUP.

    The table's 5s poll already keeps the highlight correct; this only
    decides whether it lands with the picture or up to 5s later
    (#3177), so any failure just ends the task.

    Fan-out goes through ``group_send`` rather than straight to a
    socket, which is what lets this be a background task at all:
    Channels dispatches a consumer's handlers one at a time, so a send
    from outside that loop could interleave with an ``asset_update``.
    """
    layer = get_channel_layer()
    if layer is None:
        return
    client = None
    try:
        client = connect_to_redis_async()
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(now_playing.NOW_PLAYING_CHANNEL)
        _warn.worked('subscription')
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=_SUBSCRIPTION_POLL_S,
            )
            if message is None:
                continue
            # Payload dropped, not forwarded: vendor.ts fires htmx
            # refresh-assets on any message and never reads the body,
            # so the id buys it nothing on an endpoint that has no auth
            # and, under ALLOWED_HOSTS=['*'], no working origin check.
            # This narrows the exposure rather than closing it:
            # notify_asset_update still carries real ids on every write,
            # and the frame's timing still marks each rotation. Closing
            # it means auth on /ws.
            await layer.group_send(
                WS_GROUP, {'type': 'asset_update', 'asset_id': '*'}
            )
    except Exception as exc:
        # Latched rather than DEBUG: "no Redis" is expected and stays
        # one line, but a redis-py API change would otherwise disable
        # the push with nothing in the journal, and the tests mock the
        # client end to end. CancelledError is a BaseException, so an
        # ordinary teardown does not land here.
        _warn.warn(
            'subscription',
            'Now-playing push unavailable; browsers fall back to the 5s '
            'schedule-table poll',
            exc,
        )
    finally:
        # Enough on its own: the client owns the subscription's pool,
        # and aclose() disconnects in-use connections too. Suppressed
        # because this also runs on the cancellation path, where a
        # raise would become the task's unretrieved result.
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()


class AssetConsumer(AsyncWebsocketConsumer):
    async def connect(self) -> None:
        await self.channel_layer.group_add(WS_GROUP, self.channel_name)
        await self.accept()
        self._holds_now_playing_watcher = True
        _acquire_now_playing_watcher()

    async def disconnect(self, code: int) -> None:
        try:
            # First: leaving a dead channel name in the group means
            # every later notify_asset_update fans out to it.
            await self.channel_layer.group_discard(WS_GROUP, self.channel_name)
        finally:
            # In a finally because group_discard raises when Redis is
            # unreachable, and Channels lets that escape rather than
            # reaching StopConsumer. Skipping the release would ratchet
            # _open_sockets up for good, leaving the subscription alive
            # with no sockets behind it -- the exact thing the refcount
            # exists to bound. Guarded because disconnect() also runs
            # for a socket that never finished connect().
            if getattr(self, '_holds_now_playing_watcher', False):
                self._holds_now_playing_watcher = False
                _release_now_playing_watcher()

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
