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

#: Ceiling on one read, not a delay: get_message returns the moment a
#: message lands, so this costs nothing in latency. Its only job is to
#: re-enter parse_response, because that is where PubSub.check_health
#: runs and a blocking listen() would never get there. Matched to the
#: client's health_check_interval, so an idle connection is probed
#: about twice a minute rather than woken sixty times.
#:
#: The loop depends on an expired read returning None rather than
#: raising: read_response turns asyncio.TimeoutError into None when the
#: timeout was passed explicitly, and only raises when it came from
#: socket_timeout. Without that an idle device would kill the task
#: every 30s.
_SUBSCRIPTION_POLL_S = 30.0

#: Backoff bounds for re-establishing a subscription that dropped.
#: What is bounded is the delay, not the number of attempts: the task
#: only exists while a browser holds a socket open
#: (``_release_now_playing_watcher`` cancels it at zero), so an attempt
#: ceiling would reinstate exactly the bug this loop fixes — an outage
#: that outlasts the budget leaving every already-open tab poll-only
#: until someone reloads the page. Capping the delay instead means a
#: Redis that has been down all night costs one dial a minute rather
#: than one a second.
_RETRY_MIN_S = 1.0
_RETRY_MAX_S = 60.0

#: How long an attempt has to survive before it counts as a recovery
#: rather than one more cycle of a flapping Redis. Both the backoff
#: reset and the warn-once re-arm hang off it. Without it, a server
#: that accepts SUBSCRIBE and then drops the connection a second later
#: would reset the backoff and re-arm the latch on every pass, turning
#: one fault into a WARNING per second — the journal-flooding that
#: :mod:`anthias_common.warn_once` exists to prevent (#3268).
_STABLE_AFTER_S = _RETRY_MAX_S

#: The process's single now-playing subscriber, and the sockets
#: relying on it. Process-wide rather than per socket: /ws has no auth
#: and vendor.ts opens it on every page, so per-socket would let
#: anything that can reach the device claim a Redis connection and an
#: event-loop task per socket it opens. Held by a set of channel names
#: rather than a counter, so a double release, or a connect whose
#: disconnect never ran, is idempotent instead of leaving the
#: arithmetic permanently off.
#:
#: Per *process*, so this is one subscriber per device only while
#: bin/start_server.sh runs uvicorn without --workers; N workers would
#: mean N group_sends per rotation, each fanning out to the whole
#: shared group.
_now_playing_watcher: 'asyncio.Task[None] | None' = None
_watchers_wanted: set[str] = set()


def _acquire_now_playing_watcher(channel_name: str) -> None:
    """Start the subscriber if this is the first socket to need it.

    Restarts a finished task too. That is a backstop rather than the
    recovery path: the body retries a dropped subscription itself, so
    it now only ends on cancellation or on there being no channel
    layer to send to. Recovering from a Redis outage must not depend
    on a new ``connect()``, because the browser's socket terminates at
    uvicorn rather than at Redis — a blip never closes it, so
    ``vendor.ts`` never reconnects and nothing would trigger the
    restart (SIRI-61).
    """
    global _now_playing_watcher
    _watchers_wanted.add(channel_name)
    task = _now_playing_watcher
    # cancelling() as well as done(): a task that has been asked to
    # stop but has not unwound yet is on its way out, and handing it
    # back to a browser that just arrived would leave that browser on
    # poll-only for good.
    if task is not None and not task.done() and not task.cancelling():
        return
    _now_playing_watcher = asyncio.create_task(_watch_now_playing())


def _release_now_playing_watcher(channel_name: str) -> None:
    """Stop the subscriber once the last socket has gone.

    Cancelled, not awaited: a cancelled task is not an unretrieved
    exception, so this costs no asyncio ERROR log (and so no Sentry
    event), and the task's ``finally`` closes the client on the next
    pass. Stopping at zero also leaves nothing pending at shutdown.

    The reference is kept rather than dropped, because the event loop
    holds only a weak one and the task still has an ``await`` to run
    in its ``finally``.
    """
    _watchers_wanted.discard(channel_name)
    if _watchers_wanted or _now_playing_watcher is None:
        return
    _now_playing_watcher.cancel()


async def _subscribe_and_forward(layer: Any) -> None:
    """One attempt: subscribe, then forward until something breaks.

    Only ever leaves by raising (or by being cancelled) — the caller
    treats a return as the end of an attempt either way.

    Fan-out goes through ``group_send`` rather than straight to a
    socket, which is what lets this be a background task at all:
    Channels dispatches a consumer's handlers one at a time, so a send
    from outside that loop could interleave with an ``asset_update``.
    """
    started = asyncio.get_running_loop().time()
    settled = False
    client = connect_to_redis_async()
    try:
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(now_playing.NOW_PLAYING_CHANNEL)
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=_SUBSCRIPTION_POLL_S,
            )
            if not settled:
                # Re-armed here, once the subscription has held for
                # _STABLE_AFTER_S, rather than the moment SUBSCRIBE
                # returns: a connection that is accepted and then
                # dropped is the fault continuing, not a recovery, and
                # re-arming on it would warn again on the next pass.
                # The read has a ceiling, so this is reached within a
                # poll interval of the mark even on a silent device.
                elapsed = asyncio.get_running_loop().time() - started
                if elapsed >= _STABLE_AFTER_S:
                    _warn.worked('subscription')
                    settled = True
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
    finally:
        # Enough on its own: the client owns the subscription's pool,
        # and aclose() disconnects in-use connections too. Suppressed
        # because this also runs on the cancellation path, where a
        # raise would become the task's unretrieved result. Per
        # attempt, so a retry never leaks the dropped connection.
        with contextlib.suppress(Exception):
            await client.aclose()


async def _watch_now_playing() -> None:
    """Bridge the viewer's now-playing announcements onto WS_GROUP.

    The table's 5s poll already keeps the highlight correct; this only
    decides whether it lands with the picture or up to 5s later
    (#3177), so a failure costs latency rather than correctness.

    It still has to recover on its own, though. A dropped subscription
    used to end the task, leaving the restart to the next WebSocket
    ``connect()`` — an event uncorrelated with Redis coming back,
    since the browser's socket terminates at uvicorn and a blip never
    closes it. So every tab open across a ``docker compose restart
    redis`` stayed poll-only until it was reloaded (SIRI-61). Retrying
    in the body ties the recovery to the fault instead.

    A failed ``group_send`` re-enters the same loop: the channel layer
    points at the same Redis, so it is the same outage, and rebuilding
    both halves keeps this to one recovery path.
    """
    layer = get_channel_layer()
    if layer is None:
        return
    delay = _RETRY_MIN_S
    while True:
        started = asyncio.get_running_loop().time()
        try:
            await _subscribe_and_forward(layer)
        except Exception as exc:
            # Latched rather than DEBUG: "no Redis" is expected and
            # stays one line for the whole outage, but a redis-py API
            # change would otherwise disable the push with nothing in
            # the journal, and the tests mock the client end to end.
            # CancelledError is a BaseException, so an ordinary
            # teardown does not land here — and does not get retried.
            _warn.warn(
                'subscription',
                'Now-playing push unavailable; browsers fall back to the '
                '5s schedule-table poll',
                exc,
            )
        # An attempt that held long enough to count as healthy starts
        # the next outage at the floor; anything shorter is the same
        # outage still going, so its wait keeps doubling. Reset before
        # the sleep, so the first retry after a night of uptime is
        # prompt rather than inheriting the last outage's ceiling.
        lasted = asyncio.get_running_loop().time() - started
        if lasted >= _STABLE_AFTER_S:
            delay = _RETRY_MIN_S
        # Cancellable: _release_now_playing_watcher fires when the last
        # tab closes, and CancelledError unwinds out of the sleep
        # rather than being caught above, so a device with no browser
        # on it is not left waiting out a backoff.
        await asyncio.sleep(delay)
        delay = min(delay * 2, _RETRY_MAX_S)


class AssetConsumer(AsyncWebsocketConsumer):
    async def connect(self) -> None:
        await self.channel_layer.group_add(WS_GROUP, self.channel_name)
        await self.accept()
        _acquire_now_playing_watcher(self.channel_name)

    async def disconnect(self, code: int) -> None:
        try:
            # First: leaving a dead channel name in the group means
            # every later notify_asset_update fans out to it.
            await self.channel_layer.group_discard(WS_GROUP, self.channel_name)
        finally:
            # In a finally because group_discard raises when Redis is
            # unreachable, and Channels lets that escape rather than
            # reaching StopConsumer. Skipping the release would leave
            # the name in the set for good, and the subscription alive
            # with no sockets behind it. Safe for a socket that never finished
            # connect(), because discarding a name that was never
            # added is a no-op.
            _release_now_playing_watcher(self.channel_name)

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
