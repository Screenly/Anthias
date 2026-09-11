import logging
from typing import Any

from asgiref.sync import async_to_sync
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

WS_GROUP = 'ws_server'


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
        ``save()``, and the ASGI worker serving this socket is the same
        process that serves the settings page — so flipping the flag
        takes effect on the next handshake without a restart, exactly
        as it does for the HTTP views.
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
