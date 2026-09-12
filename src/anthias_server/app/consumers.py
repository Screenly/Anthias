import logging
from typing import Any

from asgiref.sync import async_to_sync
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

WS_GROUP = 'ws_server'


def _is_after_close_race(message: str, asgi_message: str) -> bool:
    """True when ``message`` is the ASGI server's "you sent X after this
    socket was already closed" RuntimeError.

    The browser can disconnect in the window between a group_send
    dispatch and the consumer acting on it, so the ASGI server has
    already emitted 'websocket.close' and raises on the next frame:
    "Unexpected ASGI message 'websocket.send', after sending
    'websocket.close' or response already completed." (Sentry
    ANTHIAS-1K).

    Both halves are required — the out-of-order message type AND the
    after-close clause — so a genuine failure (a serialization error, a
    Channels bug), even one that merely mentions websocket.close, still
    propagates instead of being hidden. Matching the full "after
    sending 'websocket.close'" phrase rather than the bare message type
    is what keeps that strict for ``asgi_message='websocket.close'``,
    where the type appears in the prefix too.
    """
    return f"Unexpected ASGI message '{asgi_message}'" in message and (
        "after sending 'websocket.close'" in message
        or 'response already completed' in message
    )


class AssetConsumer(AsyncWebsocketConsumer):
    def _is_authorized(self) -> bool:
        """WebSocket counterpart of :func:`anthias_server.lib.auth.authorized`.

        Same feature flag, same trust model, so the two surfaces can't
        drift into disagreeing about who may watch the device:

        * ``settings['auth_backend'] == ''`` — the operator has auth
          turned off and the documented contract is that the device is
          fully open. /ws follows the HTTP views rather than inventing
          a stricter rule of its own.
        * Otherwise the handshake must carry a logged-in session.
          ``AuthMiddlewareStack`` in ``django_project/asgi.py`` has
          already resolved ``scope['user']`` from the session cookie by
          the time ``connect()`` runs (``AuthMiddleware.__call__``
          awaits ``get_user`` before dispatching), so this is a plain
          in-memory attribute read — no sync-DB access from async code.

        Session cookie only, deliberately: every page that opens /ws
        extends base.html and is itself behind ``@authorized``, so an
        operator browser is the sole legitimate client. The legacy
        Basic-auth path (``DeprecatedBasicAuthentication``) is not
        honoured here — browsers can't set an Authorization header on a
        ``new WebSocket()`` handshake anyway, so accepting it would only
        widen a deprecated credential to a surface that never had it.

        ``settings`` is an in-process ``UserDict`` reloaded by
        ``save()``, and uvicorn runs the server single-worker, so the
        process that handles the settings save is the same one holding
        every open socket — re-reading the flag here sees an auth
        toggle immediately, with no restart and no cross-process
        coordination.
        """
        from anthias_server.settings import settings

        if not settings['auth_backend']:
            return True
        user = self.scope.get('user')
        return bool(user is not None and user.is_authenticated)

    async def connect(self) -> None:
        if not self._is_authorized():
            # Refuse the handshake outright instead of accepting and
            # then closing: the socket is never added to WS_GROUP, so
            # an unauthenticated listener sees neither asset ids nor
            # the *timing* of writes — which was the residual leak that
            # survived narrowing the now-playing bridge's payload to
            # '*' (SIRI-62). Closing before accept() means Channels
            # answers the upgrade with a 403 rather than a close frame,
            # so no custom close code reaches the browser; the client's
            # reconnect stays on its existing capped backoff and the
            # 5s htmx poll — which does get a 302 to /login — remains
            # the thing that tells an expired session what happened.
            logger.debug(
                'Rejected an unauthenticated /ws handshake from %r',
                self.scope.get('client'),
            )
            await self.close()
            return
        await self.channel_layer.group_add(WS_GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, code: int) -> None:
        await self.channel_layer.group_discard(WS_GROUP, self.channel_name)

    async def force_disconnect(self, event: dict[str, Any]) -> None:
        """Drop this socket because the device's auth settings changed.

        Authorization is otherwise decided once, at handshake time, so
        a socket opened while auth was off would keep streaming after
        the operator turned auth on, and a socket opened for an
        operator would outlive the credentials it was accepted under.
        :func:`disconnect_all` fans this out from the settings-save
        path so both are closed at the moment the change lands.

        Closing here (rather than letting the socket go quiet) is
        deliberate: this instant is tied to the operator's settings
        save, not to an asset write, so it reveals nothing about the
        screen. Browsers reconnect on their existing backoff and get
        re-authorized from scratch — the operator's tab picks its
        socket straight back up, an unauthenticated listener gets a
        403.
        """
        try:
            await self.close()
        except RuntimeError as exc:
            # Same disconnect race as asset_update: the client may have
            # gone away between the group_send and this close.
            if not _is_after_close_race(str(exc), 'websocket.close'):
                raise
            logger.debug(
                'force_disconnect: socket was already closed',
                exc_info=True,
            )

    async def asset_update(self, event: dict[str, Any]) -> None:
        if not self._is_authorized():
            # Re-checked per frame, not just at handshake: the
            # disconnect fan-out below is best-effort (it rides the
            # same channel layer that notify_asset_update swallows
            # errors from), so this is what actually guarantees the
            # invariant — while auth is on, no frame reaches a socket
            # that isn't authorized, however it came to still be open.
            #
            # Stay silent rather than closing. A close is itself an
            # event the listener can time, and it would land exactly on
            # the write we are refusing to disclose; going quiet leaks
            # nothing at all. force_disconnect() is what reaps the
            # socket, at a moment uncorrelated with any asset write.
            logger.debug(
                'Suppressed a /ws fan-out to an unauthorized socket from %r',
                self.scope.get('client'),
            )
            return

        # Plain text frame: the client only needs to know "something
        # changed" to fire htmx refresh-assets; carrying the full
        # changeset over WS would duplicate the partial render path.
        asset_id = event.get('asset_id', '')
        try:
            await self.send(text_data=asset_id)
        except RuntimeError as exc:
            # The browser can disconnect in the window between the
            # group_send dispatch and this send (Sentry ANTHIAS-1K).
            # group_discard runs in disconnect(), so this stale channel
            # is on its way out — drop the nudge; the client's 5s poll
            # keeps it consistent. Log at debug (with the asset_id) so
            # the race stays diagnosable without becoming a reportable
            # event.
            if not _is_after_close_race(str(exc), 'websocket.send'):
                raise
            logger.debug(
                'asset_update: send on a closed websocket for %r; client '
                'disconnected mid-broadcast',
                asset_id,
                exc_info=True,
            )


def _broadcast(message: dict[str, Any], *, description: str) -> None:
    """Sync wrapper around channels.layers.group_send so Django views
    and Celery tasks can fan out to every open socket without going
    through asyncio."""
    layer = get_channel_layer()
    if layer is None:
        # No CHANNEL_LAYERS configured — quietly skip rather than
        # 500ing the request. The 5s poll still keeps the table
        # eventually-consistent, and AssetConsumer re-checks
        # authorization per frame regardless.
        return
    try:
        async_to_sync(layer.group_send)(WS_GROUP, message)
    except Exception:
        # Redis hiccup / channel-layer outage — log and let the caller
        # carry on; the poll fallback covers correctness.
        logger.exception('%s failed', description)


def notify_asset_update(asset_id: str = '*') -> None:
    """Fan-out a 'refresh' nudge to every connected browser.

    Pass the affected asset_id when known; '*' is a generic "table
    state changed" sentinel for write paths that touch many rows at
    once (reorder, settings save, ...).
    """
    _broadcast(
        {'type': 'asset_update', 'asset_id': asset_id},
        description=f'notify_asset_update for {asset_id}',
    )


def disconnect_all() -> None:
    """Close every open /ws socket.

    Called from the settings-save paths when the auth settings actually
    changed, so authorization is re-decided from scratch instead of
    being frozen at whatever it was when each socket was opened. See
    :meth:`AssetConsumer.force_disconnect` for why closing (rather than
    going quiet) is the right move at this particular moment.
    """
    _broadcast(
        {'type': 'force_disconnect'},
        description='disconnect_all',
    )
