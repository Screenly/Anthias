import asyncio
from typing import Any
from unittest import mock

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from asgiref.testing import ApplicationCommunicator
from django.contrib.auth.models import AnonymousUser, User
from django.test import Client, override_settings

from anthias_server.app.consumers import AssetConsumer, notify_asset_update


def test_asset_update_sends_asset_id() -> None:
    """The happy path forwards the asset_id as the text frame so the
    browser's htmx handler knows which row changed."""
    consumer = AssetConsumer()
    send = mock.AsyncMock()

    with mock.patch.object(consumer, 'send', send):
        asyncio.run(consumer.asset_update({'asset_id': 'abc123'}))

    send.assert_awaited_once_with(text_data='abc123')


def test_asset_update_swallows_send_after_close() -> None:
    """A browser can disconnect between the group_send dispatch and this
    send, so channels raises RuntimeError("Unexpected ASGI message
    'websocket.send', after sending 'websocket.close'"). The nudge is
    best-effort (the 5s poll backs it up), so the consumer must swallow
    it rather than let it reach Sentry (ANTHIAS-1K)."""
    consumer = AssetConsumer()
    send = mock.AsyncMock(
        side_effect=RuntimeError(
            "Unexpected ASGI message 'websocket.send', after sending "
            "'websocket.close' or response already completed."
        )
    )

    # Must not raise.
    with mock.patch.object(consumer, 'send', send):
        asyncio.run(consumer.asset_update({'asset_id': 'abc123'}))

    send.assert_awaited_once()


def test_asset_update_reraises_unrelated_runtime_error() -> None:
    """The swallow is scoped to the send-after-close message — any other
    RuntimeError out of send() (a serialization error, a Channels bug) is
    a genuine failure and must still propagate."""
    consumer = AssetConsumer()
    send = mock.AsyncMock(side_effect=RuntimeError('something actually broke'))

    with (
        mock.patch.object(consumer, 'send', send),
        pytest.raises(RuntimeError, match='something actually broke'),
    ):
        asyncio.run(consumer.asset_update({'asset_id': 'abc123'}))


def test_asset_update_reraises_non_close_websocket_send_error() -> None:
    """The swallow requires the close/completed clause: a RuntimeError
    that merely mentions 'websocket.send' but is not the send-after-close
    race (some other ASGI state bug) must still propagate."""
    consumer = AssetConsumer()
    send = mock.AsyncMock(
        side_effect=RuntimeError(
            "Unexpected ASGI message 'websocket.send', after sending "
            "'websocket.accept' was expected"
        )
    )

    with (
        mock.patch.object(consumer, 'send', send),
        pytest.raises(RuntimeError, match='websocket.accept'),
    ):
        asyncio.run(consumer.asset_update({'asset_id': 'abc123'}))


# ---------------------------------------------------------------------------
# connect() — the auth gate on the handshake (SIRI-62).
#
# /ws used to accept every handshake, so anyone who could reach the
# device's port could hold a socket open and watch write activity. The
# payload itself was already narrowed to a '*' sentinel on the
# now-playing path, but the *timing* of each frame still leaked when
# the screen rotated. These tests pin that the socket is now refused
# before it joins WS_GROUP, so there is no frame to time.
# ---------------------------------------------------------------------------


def _consumer_with_scope(user: object | None) -> tuple[AssetConsumer, Any]:
    """An AssetConsumer wired the way the ASGI stack wires it: a scope
    (with ``user`` already resolved by AuthMiddlewareStack, or absent
    when ``user`` is None) and a mock channel layer so group_add is
    observable. Returns ``(consumer, channel_layer)``."""
    consumer = AssetConsumer()
    consumer.scope = {'client': ('10.0.0.9', 54321)}
    if user is not None:
        consumer.scope['user'] = user
    consumer.channel_name = 'specific.abcdef!'
    layer = mock.MagicMock()
    layer.group_add = mock.AsyncMock()
    consumer.channel_layer = layer
    return consumer, layer


def _auth_backend(value: str) -> Any:
    """Patch the device settings dict the way tests/test_auth.py does."""
    return mock.patch.dict(
        'anthias_server.settings.settings.data', {'auth_backend': value}
    )


def test_connect_accepts_anonymous_when_auth_is_disabled() -> None:
    """``auth_backend == ''`` is the documented "this device is open"
    contract — the HTTP views pass every request through in that mode,
    and /ws must not invent a stricter rule that would break the
    dashboard on the default un-authenticated config."""
    consumer, layer = _consumer_with_scope(AnonymousUser())
    accept, close = mock.AsyncMock(), mock.AsyncMock()

    with (
        _auth_backend(''),
        mock.patch.object(consumer, 'accept', accept),
        mock.patch.object(consumer, 'close', close),
    ):
        asyncio.run(consumer.connect())

    layer.group_add.assert_awaited_once_with('ws_server', 'specific.abcdef!')
    accept.assert_awaited_once()
    close.assert_not_awaited()


def test_connect_rejects_anonymous_when_auth_is_enabled() -> None:
    """The actual fix: with auth on, a handshake that carries no
    logged-in session is closed *before* accept() and before the
    channel joins WS_GROUP — so an unauthenticated listener can no
    longer time the device's writes."""
    consumer, layer = _consumer_with_scope(AnonymousUser())
    accept, close = mock.AsyncMock(), mock.AsyncMock()

    with (
        _auth_backend('auth_basic'),
        mock.patch.object(consumer, 'accept', accept),
        mock.patch.object(consumer, 'close', close),
    ):
        asyncio.run(consumer.connect())

    close.assert_awaited_once()
    # Both of these are the leak: group_add would deliver the frames,
    # accept() would keep the socket open long enough to receive them.
    layer.group_add.assert_not_awaited()
    accept.assert_not_awaited()


def test_connect_accepts_authenticated_session_when_auth_is_enabled() -> None:
    """The operator's own dashboard must keep its live refresh when
    auth is on — otherwise the fix would silently downgrade every
    authenticated device to the 5s poll."""
    consumer, layer = _consumer_with_scope(mock.Mock(is_authenticated=True))
    accept, close = mock.AsyncMock(), mock.AsyncMock()

    with (
        _auth_backend('auth_basic'),
        mock.patch.object(consumer, 'accept', accept),
        mock.patch.object(consumer, 'close', close),
    ):
        asyncio.run(consumer.connect())

    layer.group_add.assert_awaited_once_with('ws_server', 'specific.abcdef!')
    accept.assert_awaited_once()
    close.assert_not_awaited()


def test_connect_rejects_when_scope_has_no_user() -> None:
    """Fail closed. A missing ``scope['user']`` means the auth
    middleware isn't in the stack (a bad refactor of asgi.py), not
    that the caller is trustworthy — treat it as unauthenticated
    rather than defaulting the whole endpoint back open."""
    consumer, layer = _consumer_with_scope(None)
    accept, close = mock.AsyncMock(), mock.AsyncMock()

    with (
        _auth_backend('auth_basic'),
        mock.patch.object(consumer, 'accept', accept),
        mock.patch.object(consumer, 'close', close),
    ):
        asyncio.run(consumer.connect())

    close.assert_awaited_once()
    layer.group_add.assert_not_awaited()
    accept.assert_not_awaited()


# ---------------------------------------------------------------------------
# End-to-end through the real ASGI stack.
#
# The unit tests above drive AssetConsumer directly, so they'd still
# pass if asgi.py stopped wrapping the router in AuthMiddlewareStack —
# scope['user'] would just be missing and every socket would be refused.
# These drive ``django_project.asgi.application`` itself with a real
# session cookie, which pins both halves: the middleware is wired, and
# a genuine operator session gets through it.
#
# asgiref's ApplicationCommunicator rather than Channels' own
# WebsocketCommunicator wrapper: importing ``channels.testing`` pulls
# in ``daphne`` via ChannelsLiveServerTestCase, and this project serves
# ASGI with uvicorn. Driving the raw ASGI messages avoids adding a
# server we don't ship just to reach a test helper.
# ---------------------------------------------------------------------------

_IN_MEMORY_LAYER = {
    'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}
}


def _communicator(
    headers: list[tuple[bytes, bytes]] | None = None,
) -> ApplicationCommunicator:
    """Drive the real ASGI app over a websocket scope shaped the way
    uvicorn shapes one."""
    # Imported lazily: asgi.py calls get_asgi_application() at import
    # time, which needs the app registry populated.
    from anthias_server.django_project.asgi import application

    return ApplicationCommunicator(
        application,
        {
            'type': 'websocket',
            'path': '/ws',
            'raw_path': b'/ws',
            'query_string': b'',
            'headers': headers or [],
            'subprotocols': [],
            'client': ('10.0.0.9', 54321),
            'server': ('127.0.0.1', 8080),
            'scheme': 'ws',
        },
    )


async def _handshake(communicator: ApplicationCommunicator) -> dict[str, Any]:
    """Send websocket.connect and return the app's verdict message —
    ``websocket.accept`` or ``websocket.close``."""
    await communicator.send_input({'type': 'websocket.connect'})
    return await communicator.receive_output(timeout=5)


def _session_cookie_header(
    username: str, password: str
) -> list[tuple[bytes, bytes]]:
    """Log in over the HTTP test client and hand the resulting session
    cookie back as a handshake header — exactly what a browser does
    when ``new WebSocket('ws://host/ws')`` fires on a page the operator
    is already signed in to."""
    client = Client()
    assert client.login(username=username, password=password)
    session_id = client.cookies['sessionid'].value
    return [(b'cookie', f'sessionid={session_id}'.encode())]


@pytest.mark.django_db
@override_settings(CHANNEL_LAYERS=_IN_MEMORY_LAYER)
def test_ws_handshake_is_refused_without_a_session() -> None:
    """The leak, closed at the edge: no session cookie, no socket."""

    async def body() -> None:
        communicator = _communicator()
        assert (await _handshake(communicator))['type'] == 'websocket.close'

    with _auth_backend('auth_basic'):
        async_to_sync(body)()


@pytest.mark.django_db
@override_settings(CHANNEL_LAYERS=_IN_MEMORY_LAYER)
def test_ws_handshake_with_a_session_still_receives_updates() -> None:
    """Mutation check on the fix: an authenticated operator keeps the
    live refresh. Asserting on a real delivered frame — not just on a
    successful handshake — is what makes this fail if the gate were
    ever 'fixed' by dropping the broadcast instead of the socket."""
    User.objects.create_user(username='alice', password='s3cret-pa55phrase')
    # Log in out here: Client.login() hits the DB synchronously, which
    # Django refuses from inside a running event loop.
    headers = _session_cookie_header('alice', 's3cret-pa55phrase')

    async def body() -> None:
        communicator = _communicator(headers)
        assert (await _handshake(communicator))['type'] == 'websocket.accept'
        # notify_asset_update is sync (views and Celery tasks call it
        # that way), and wraps group_send in async_to_sync. Reach it
        # through sync_to_async so the nesting asgiref supports is what
        # gets exercised, and the channel layer stays on one loop.
        await sync_to_async(notify_asset_update, thread_sensitive=True)('*')
        frame = await communicator.receive_output(timeout=5)
        assert frame == {'type': 'websocket.send', 'text': '*'}

    with _auth_backend('auth_basic'):
        async_to_sync(body)()


@pytest.mark.django_db
@override_settings(CHANNEL_LAYERS=_IN_MEMORY_LAYER)
def test_ws_handshake_is_open_when_auth_is_disabled() -> None:
    """Default config (auth off) is untouched — the dashboard on a
    device that was never given credentials keeps its socket."""

    async def body() -> None:
        communicator = _communicator()
        assert (await _handshake(communicator))['type'] == 'websocket.accept'

    with _auth_backend(''):
        async_to_sync(body)()
