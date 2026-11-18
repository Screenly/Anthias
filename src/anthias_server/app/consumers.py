import logging
import threading
from typing import Any
from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

WS_GROUP = 'ws_server'

# Bumped whenever a settings save invalidates credentials that are
# already in use. Each socket records the value it was accepted under
# and is refused from the next frame onwards once the two disagree, so
# a revocation fails closed inside this process rather than depending
# on the force_disconnect fan-out actually arriving. See
# disconnect_all(), which is the only thing that bumps it.
#
# In-process is enough for every surface that serves HTTP, for the
# same reason ``settings`` being an in-process UserDict is: uvicorn
# serves this app single-worker (see bin/start_server.sh), so the
# process that handles a settings save — or an /admin password change
# — is the one holding every open socket.
#
# It is therefore *only* those surfaces that fail closed. A credential
# change made from another process (``manage.py changepassword``, a
# shell on the device) bumps that process's copy of this counter,
# which reaches nobody; all it has is the best-effort Redis fan-out in
# disconnect_all(). Closing that gap would mean re-reading credential
# state from the DB on a timer or per frame in the serving process,
# which is exactly the SQLite/SBC cost this counter exists to avoid.
_auth_generation = 0

# Identifies this process in the events it publishes, so a consumer
# can tell "my process bumped the counter" (where comparing
# generations is meaningful) from "some other process did" (where it
# is not). Regenerated per process, deliberately: two workers must not
# share one.
_PROCESS_ID = uuid4().hex

# Guards the increment only. See disconnect_all().
_auth_generation_lock = threading.Lock()


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


def stamp_auth_generation(app: Any) -> Any:
    """ASGI middleware that records the revocation generation *before*
    the session lookup.

    ``AuthMiddlewareStack`` resolves ``scope['user']`` and only then
    dispatches to the consumer, so a rotation committing inside that
    window would be missed: the user came from a session that was
    still valid when it was read, while the consumer — constructed
    afterwards — would read the already-bumped counter, find it
    current, and keep the socket for good. Stamping on the way in
    means such a handshake carries the pre-rotation generation and is
    refused, which is the fail-closed direction.

    Installed outside ``AuthMiddlewareStack`` in ``asgi.py``; the
    consumer falls back to the module counter when the stamp is
    absent, so a directly-constructed consumer (unit tests) still
    starts out current.
    """

    async def middleware(scope: Any, receive: Any, send: Any) -> Any:
        scope = dict(scope)
        scope['auth_generation'] = _auth_generation
        return await app(scope, receive, send)

    return middleware


class AssetConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Channels builds one consumer per connection, so this is
        # effectively the handshake instant. Recorded in __init__
        # rather than in connect() so a consumer constructed directly
        # (unit tests, and any future non-handshake use) starts out
        # current instead of inheriting a stale class-level default and
        # going silent for reasons that have nothing to do with it.
        self._auth_generation = _auth_generation

    def _accepted_generation(self) -> int:
        """The generation this socket was accepted under.

        The scope stamp when there is one — it was taken before the
        session lookup, so it cannot miss a rotation that landed
        during it — and the value captured at construction otherwise.
        """
        scope = getattr(self, 'scope', None)
        stamped = (
            scope.get('auth_generation') if isinstance(scope, dict) else None
        )
        if isinstance(stamped, int):
            return stamped
        return self._auth_generation

    def _is_authorized(self) -> bool:
        """WebSocket counterpart of :func:`anthias_server.lib.auth.authorized`.

        Same feature flag, same trust model, so the two surfaces can't
        drift into disagreeing about who may watch the device:

        * ``settings['auth_backend'] == ''`` — the operator has auth
          turned off and the documented contract is that the device is
          fully open. /ws follows the HTTP views rather than inventing
          a stricter rule of its own. The generation check is skipped
          in this mode deliberately: with no credentials to revoke, a
          socket that missed its ``force_disconnect`` should keep
          working rather than be stranded on the 5s poll for the rest
          of its life.
        * The socket's auth generation must still be current. A
          credential rotation does not change the already-resolved
          ``scope['user']`` — ``is_authenticated`` is True for any real
          User row, whatever its password now is — so on its own the
          session check below would keep passing for a socket accepted
          under the old password. The generation compared is the one
          stamped on the scope before the session was resolved (see
          ``stamp_auth_generation``), so a handshake that straddles a
          rotation is refused rather than accepted under credentials
          that stopped being current while it was in flight.
          ``disconnect_all()`` closes those,
          but that fan-out is best-effort (``_broadcast`` swallows
          channel-layer failures, and a transient Redis blip during the
          save would drop it while later asset writes still get
          through). Comparing generations is what makes revocation fail
          closed: no DB hit per frame, and no dependence on the channel
          layer being healthy at the moment the operator saves.
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
        if self._accepted_generation() != _auth_generation:
            return False
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

        A socket accepted *after* the bump this event belongs to is
        left alone: it joined the group in the window between the bump
        and the publish, already stamped with the new generation, so
        the revocation never applied to it and closing it would drop
        an operator reconnect for nothing. Only this process's events
        can be compared that way — another process's counter is
        unrelated to ours, so those still close everything.
        """
        generation = event.get('generation')
        if (
            event.get('origin') == _PROCESS_ID
            and isinstance(generation, int)
            and self._accepted_generation() >= generation
        ):
            logger.debug(
                'force_disconnect: socket is newer than the revocation'
            )
            return
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
            # That covers both ways a socket can outlive its
            # authorization: the operator turning auth on (caught by
            # re-reading the flag) and a credential rotation (caught by
            # the auth generation), neither of which needs the close to
            # have been delivered.
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

    The generation bump comes first and is the part that cannot fail:
    the close below rides the channel layer and is swallowed if that is
    down, whereas bumping the counter takes effect immediately. The
    close is the courteous half (the operator's browser re-handshakes
    at once and keeps its live refresh); the bump is the half that
    holds the security property.

    What the bump silences is every already-open socket *while
    authentication is enabled* — a save that turns auth off bumps the
    counter too, but ``_is_authorized`` returns True before it looks at
    the generation in that mode, so a socket that misses the close on
    an auth-off save keeps working rather than being stranded on the 5s
    poll. That is the open-device contract, not an oversight.

    Called from the settings-save paths for an ``auth_backend`` toggle,
    and from the User post_save/post_delete receiver in ``signals.py``
    for a credential change — the latter so a rotation is revoked
    wherever it comes from, and atomically with the DB write rather
    than after it. In-process callers (the settings page, the v2 API,
    ``/admin``) get the generation bump as well as the close, so they
    fail closed; an out-of-process caller has only the close. See the
    note on ``_auth_generation``.
    """
    global _auth_generation
    # Under the lock: uvicorn runs sync views in a threadpool, so two
    # credential changes can land on two threads at once, and `+= 1`
    # is load-add-store rather than one bytecode. A lost update would
    # leave a socket stamped between the two changes matching the
    # final value — still receiving frames after a revocation.
    #
    # CPython's GIL makes that interleaving unobservable in practice
    # today (measured: no lost update in 480k racing increments with
    # the switch interval at 1 us), so this is not a fix for a bug
    # anyone has seen. It is here because the language does not
    # promise it and a free-threaded build does not have the GIL to
    # lean on — and an uncontended lock costs nothing on a path that
    # runs once per credential change. Reads stay lock-free; a single
    # attribute load can't tear.
    with _auth_generation_lock:
        _auth_generation += 1
        generation = _auth_generation
    # The event carries which bump it belongs to, and which process
    # made it. A handshake completing between the bump above and this
    # publish joins the group already stamped with the new generation
    # — it was accepted *under* the new credentials — and closing it
    # would drop the operator's reconnect for a revocation that never
    # applied to it. force_disconnect() uses the two fields to tell
    # those apart, and closes unconditionally for any other process,
    # whose counter means nothing here.
    _broadcast(
        {
            'type': 'force_disconnect',
            'origin': _PROCESS_ID,
            'generation': generation,
        },
        description='disconnect_all',
    )
