import asyncio
import contextlib
from typing import Any
from unittest import mock

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from asgiref.testing import ApplicationCommunicator
from django.contrib.auth.models import AnonymousUser, User
from django.db import transaction
from django.test import Client, override_settings
from django.utils import timezone

from anthias_server.app import consumers as consumers_module
from anthias_server.app.consumers import (
    AssetConsumer,
    disconnect_all,
    notify_asset_update,
)


@pytest.fixture(autouse=True)
def _reset_auth_generation() -> Any:
    """``consumers._auth_generation`` is process-global by design — a
    real device's counter only ever climbs. Restore it between tests so
    a test that rotates credentials can't leave every socket built by a
    later test looking stale."""
    original = consumers_module._auth_generation
    yield
    consumers_module._auth_generation = original


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


# ---------------------------------------------------------------------------
# Authorization outliving the handshake (Copilot review on PR 3324).
#
# connect() decides authorization once. On its own that leaves two
# sockets alive that shouldn't be: one opened while auth was off and
# still attached after the operator turned auth on, and one opened for
# an operator whose credentials were then rotated. Two mechanisms close
# that gap and these tests pin both.
# ---------------------------------------------------------------------------


def test_asset_update_is_suppressed_once_auth_is_enabled() -> None:
    """The socket was accepted while auth was off. Turning auth on must
    stop the fan-out reaching it — otherwise the exact leak this issue
    is about survives, just on a connection that predates the switch."""
    consumer, _ = _consumer_with_scope(AnonymousUser())
    send = mock.AsyncMock()
    close = mock.AsyncMock()

    with (
        _auth_backend('auth_basic'),
        mock.patch.object(consumer, 'send', send),
        mock.patch.object(consumer, 'close', close),
    ):
        asyncio.run(consumer.asset_update({'asset_id': 'abc123'}))

    send.assert_not_awaited()
    # Deliberately silent rather than closed: a close is itself an
    # event the listener could time, and it would land exactly on the
    # write we are declining to disclose.
    close.assert_not_awaited()


def test_asset_update_still_delivers_to_an_authorized_socket() -> None:
    """The per-frame re-check must not cost the operator their live
    refresh while auth is on."""
    consumer, _ = _consumer_with_scope(mock.Mock(is_authenticated=True))
    send = mock.AsyncMock()

    with (
        _auth_backend('auth_basic'),
        mock.patch.object(consumer, 'send', send),
    ):
        asyncio.run(consumer.asset_update({'asset_id': 'abc123'}))

    send.assert_awaited_once_with(text_data='abc123')


def test_force_disconnect_closes_the_socket() -> None:
    """disconnect_all() fans this out from the settings-save path so
    every socket re-handshakes against the new auth state."""
    consumer, _ = _consumer_with_scope(AnonymousUser())
    close = mock.AsyncMock()

    with mock.patch.object(consumer, 'close', close):
        asyncio.run(consumer.force_disconnect({'type': 'force_disconnect'}))

    close.assert_awaited_once()


def test_force_disconnect_swallows_close_after_close() -> None:
    """Same disconnect race as asset_update (Sentry ANTHIAS-1K): the
    client can vanish between the group_send and this close."""
    consumer, _ = _consumer_with_scope(AnonymousUser())
    close = mock.AsyncMock(
        side_effect=RuntimeError(
            "Unexpected ASGI message 'websocket.close', after sending "
            "'websocket.close' or response already completed."
        )
    )

    # Must not raise.
    with mock.patch.object(consumer, 'close', close):
        asyncio.run(consumer.force_disconnect({'type': 'force_disconnect'}))

    close.assert_awaited_once()


def test_force_disconnect_reraises_an_unrelated_runtime_error() -> None:
    """The swallow stays scoped to the after-close race."""
    consumer, _ = _consumer_with_scope(AnonymousUser())
    close = mock.AsyncMock(
        side_effect=RuntimeError('something actually broke')
    )

    with (
        mock.patch.object(consumer, 'close', close),
        pytest.raises(RuntimeError, match='something actually broke'),
    ):
        asyncio.run(consumer.force_disconnect({'type': 'force_disconnect'}))


@pytest.mark.django_db
@override_settings(CHANNEL_LAYERS=_IN_MEMORY_LAYER)
def test_enabling_auth_drops_a_socket_opened_while_auth_was_off() -> None:
    """The regression test Copilot asked for, end-to-end: open an
    anonymous socket with auth off, flip auth on, and the socket must
    be both closed and silent."""

    async def body() -> None:
        communicator = _communicator()
        with _auth_backend(''):
            handshake = await _handshake(communicator)
        assert handshake['type'] == 'websocket.accept'

        with _auth_backend('auth_basic'):
            # 1. The settings-save path reaps it.
            await sync_to_async(disconnect_all, thread_sensitive=True)()
            closed = await communicator.receive_output(timeout=5)
            assert closed['type'] == 'websocket.close'

            # 2. And even if that fan-out had been lost, a write would
            #    not have reached it.
            await sync_to_async(notify_asset_update, thread_sensitive=True)(
                '*'
            )
            assert await communicator.receive_nothing(timeout=1)

    async_to_sync(body)()


@pytest.mark.django_db
@override_settings(CHANNEL_LAYERS=_IN_MEMORY_LAYER)
def test_an_authorized_socket_survives_until_auth_settings_change() -> None:
    """The mirror image: while auth is on and the session holds, the
    operator's socket keeps working — the re-check must not be a
    blanket kill switch."""
    User.objects.create_user(username='alice', password='s3cret-pa55phrase')
    headers = _session_cookie_header('alice', 's3cret-pa55phrase')

    async def body() -> None:
        communicator = _communicator(headers)
        assert (await _handshake(communicator))['type'] == 'websocket.accept'
        await sync_to_async(notify_asset_update, thread_sensitive=True)('*')
        frame = await communicator.receive_output(timeout=5)
        assert frame == {'type': 'websocket.send', 'text': '*'}

        # ...and the settings-save fan-out still reaps it, so a
        # credential rotation can't be outlived either.
        await sync_to_async(disconnect_all, thread_sensitive=True)()
        closed = await communicator.receive_output(timeout=5)
        assert closed['type'] == 'websocket.close'

    with _auth_backend('auth_basic'):
        async_to_sync(body)()


# ---------------------------------------------------------------------------
# Credential rotation fails closed even when the close is lost
# (follow-up Copilot review on PR 3324).
#
# disconnect_all() rides the same best-effort channel layer _broadcast
# swallows errors from, so the close frame can simply never arrive — a
# Redis blip during the settings save is enough. The per-frame re-check
# alone doesn't cover that case for a *rotation*: scope['user'] was
# resolved at handshake and its is_authenticated stays True however the
# password changes underneath it. The auth generation is what closes
# that, in-process and with no DB hit per frame.
# ---------------------------------------------------------------------------


def _lost_fan_out() -> Any:
    """Make the force_disconnect fan-out vanish, the way a channel-layer
    outage does — _broadcast() logs and swallows, so the caller can't
    tell. Everything disconnect_all() does *outside* the broadcast must
    still be enough on its own."""
    return mock.patch('anthias_server.app.consumers._broadcast')


def test_asset_update_is_suppressed_after_a_rotation_loses_the_close() -> None:
    """The gap Copilot found: an operator socket whose credentials were
    rotated, whose close never arrived, and whose user object still
    reports is_authenticated. It must go silent anyway."""
    consumer, _ = _consumer_with_scope(mock.Mock(is_authenticated=True))
    send = mock.AsyncMock()
    close = mock.AsyncMock()

    with (
        _auth_backend('auth_basic'),
        _lost_fan_out(),
        mock.patch.object(consumer, 'send', send),
        mock.patch.object(consumer, 'close', close),
    ):
        disconnect_all()
        asyncio.run(consumer.asset_update({'asset_id': 'abc123'}))

    send.assert_not_awaited()
    # Silent, not closed — same reasoning as the auth-toggle path: a
    # close lands exactly on the write we're declining to disclose.
    close.assert_not_awaited()


def test_a_socket_opened_after_the_rotation_still_receives_updates() -> None:
    """Mutation check: the generation must gate *stale* sockets, not
    become a permanent kill switch on every socket after the first
    credential change of the process's life."""
    with _lost_fan_out():
        disconnect_all()

    # Built after the bump, so it carries the current generation.
    consumer, _ = _consumer_with_scope(mock.Mock(is_authenticated=True))
    send = mock.AsyncMock()

    with (
        _auth_backend('auth_basic'),
        mock.patch.object(consumer, 'send', send),
    ):
        asyncio.run(consumer.asset_update({'asset_id': 'abc123'}))

    send.assert_awaited_once_with(text_data='abc123')


def test_a_stale_socket_keeps_working_while_auth_is_disabled() -> None:
    """Turning auth *off* also bumps the generation, but there are no
    credentials left to revoke — the documented contract is that the
    device is open. A socket that missed its close must not be
    stranded on the 5s poll for the rest of its life."""
    consumer, _ = _consumer_with_scope(AnonymousUser())
    send = mock.AsyncMock()

    with (
        _auth_backend(''),
        _lost_fan_out(),
        mock.patch.object(consumer, 'send', send),
    ):
        disconnect_all()
        asyncio.run(consumer.asset_update({'asset_id': 'abc123'}))

    send.assert_awaited_once_with(text_data='abc123')


def _stamped_scope() -> dict[str, Any]:
    """The scope as the ASGI stack hands it down: stamped on the way
    in, before AuthMiddlewareStack resolves the user."""
    captured: dict[str, Any] = {}

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        captured.update(scope)

    app = consumers_module.stamp_auth_generation(inner)
    asyncio.run(app({'type': 'websocket'}, mock.AsyncMock(), mock.AsyncMock()))
    return captured


def test_a_handshake_that_straddles_a_rotation_is_refused() -> None:
    """AuthMiddlewareStack resolves the user and only then builds the
    consumer. A rotation committing inside that window would leave the
    consumer reading the already-bumped counter — current — while its
    user came from a session that stopped being valid mid-handshake,
    and the socket would stay authorized for good.

    The stamp is taken before the lookup, so the handshake carries the
    pre-rotation generation and is refused. The consumer here is built
    *after* the bump on purpose: without the stamp it would be accepted.
    """
    scope = _stamped_scope()
    with _lost_fan_out():
        disconnect_all()

    consumer, _ = _consumer_with_scope(mock.Mock(is_authenticated=True))
    consumer.scope['auth_generation'] = scope['auth_generation']
    accept, close = mock.AsyncMock(), mock.AsyncMock()

    with (
        _auth_backend('auth_basic'),
        mock.patch.object(consumer, 'accept', accept),
        mock.patch.object(consumer, 'close', close),
    ):
        asyncio.run(consumer.connect())

    close.assert_awaited_once()
    accept.assert_not_awaited()


def test_a_handshake_with_no_rotation_in_flight_is_accepted() -> None:
    """Mutation check: the stamp must refuse only the straddling
    handshake, not every stamped one."""
    scope = _stamped_scope()

    consumer, _ = _consumer_with_scope(mock.Mock(is_authenticated=True))
    consumer.scope['auth_generation'] = scope['auth_generation']
    accept, close = mock.AsyncMock(), mock.AsyncMock()

    with (
        _auth_backend('auth_basic'),
        mock.patch.object(consumer, 'accept', accept),
        mock.patch.object(consumer, 'close', close),
    ):
        asyncio.run(consumer.connect())

    accept.assert_awaited_once()
    close.assert_not_awaited()


def test_the_stamp_does_not_mutate_the_servers_scope() -> None:
    """The ASGI server owns the scope it passes in; stamping a copy
    keeps a second connection from inheriting this one's generation."""
    original: dict[str, Any] = {'type': 'websocket'}

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        assert scope['auth_generation'] == consumers_module._auth_generation

    app = consumers_module.stamp_auth_generation(inner)
    asyncio.run(app(original, mock.AsyncMock(), mock.AsyncMock()))

    assert original == {'type': 'websocket'}


def test_connect_is_unaffected_by_earlier_generations() -> None:
    """A fresh handshake after any number of rotations must still be
    decided on the session alone."""
    with _lost_fan_out():
        disconnect_all()
        disconnect_all()

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


@pytest.mark.django_db
@override_settings(CHANNEL_LAYERS=_IN_MEMORY_LAYER)
def test_rotation_silences_a_live_socket_end_to_end() -> None:
    """The same thing through the real ASGI stack with a genuine
    session cookie: a socket that is receiving frames, a rotation whose
    close is dropped, and no further frame after it."""
    User.objects.create_user(username='alice', password='s3cret-pa55phrase')
    headers = _session_cookie_header('alice', 's3cret-pa55phrase')

    async def body() -> None:
        communicator = _communicator(headers)
        assert (await _handshake(communicator))['type'] == 'websocket.accept'
        await sync_to_async(notify_asset_update, thread_sensitive=True)('*')
        assert (await communicator.receive_output(timeout=5)) == {
            'type': 'websocket.send',
            'text': '*',
        }

        with _lost_fan_out():
            await sync_to_async(disconnect_all, thread_sensitive=True)()
        # The close never arrived...
        assert await communicator.receive_nothing(timeout=1)
        # ...and the socket is still silent on the next real write.
        await sync_to_async(notify_asset_update, thread_sensitive=True)('*')
        assert await communicator.receive_nothing(timeout=1)

    with _auth_backend('auth_basic'):
        async_to_sync(body)()


# ---------------------------------------------------------------------------
# Revocation is hooked to the User row, not to the settings page
# (second Copilot review on PR 3336).
#
# disconnect_all() being called from the two settings-save views left
# two holes: /admin is a routed URL whose stock UserAdmin ships a
# change-password form, and a settings save can fail *after*
# apply_auth_settings() has already persisted the rotated row. Hooking
# post_save/post_delete on User closes both — a rotation revokes
# wherever it comes from, and it does so atomically with the DB write.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_bare_user_save_revokes_authorization(
    django_capture_on_commit_callbacks: Any,
) -> None:
    """The /admin change-password form, `manage.py changepassword` and a
    shell all end at ``User.save()`` — so that is where the revocation
    hangs, rather than on the settings views none of them go through."""
    user = User.objects.create_user(username='alice', password='s3cret-pa55')
    consumer, _ = _consumer_with_scope(user)
    send = mock.AsyncMock()

    with (
        _auth_backend('auth_basic'),
        _lost_fan_out(),
        mock.patch.object(consumer, 'send', send),
    ):
        with django_capture_on_commit_callbacks(execute=True):
            user.set_password('a-rotated-pa55phrase')
            user.save(update_fields=['password'])
        asyncio.run(consumer.asset_update({'asset_id': 'abc123'}))

    send.assert_not_awaited()


@pytest.mark.django_db
def test_deleting_the_operator_revokes_authorization(
    django_capture_on_commit_callbacks: Any,
) -> None:
    """``scope['user']`` outlives the row it was resolved from, so a
    deleted account would otherwise keep its socket streaming."""
    user = User.objects.create_user(username='alice', password='s3cret-pa55')
    consumer, _ = _consumer_with_scope(user)
    send = mock.AsyncMock()

    with (
        _auth_backend('auth_basic'),
        _lost_fan_out(),
        mock.patch.object(consumer, 'send', send),
    ):
        with django_capture_on_commit_callbacks(execute=True):
            user.delete()
        asyncio.run(consumer.asset_update({'asset_id': 'abc123'}))

    send.assert_not_awaited()


@pytest.mark.django_db
def test_recording_a_login_does_not_revoke_authorization(
    django_capture_on_commit_callbacks: Any,
) -> None:
    """Django writes ``last_login`` through save(update_fields=[...]) on
    every successful login. Revoking on that would drop the operator's
    dashboard socket at the exact moment they sign in — the one User
    write that must stay neutral."""
    user = User.objects.create_user(username='alice', password='s3cret-pa55')
    consumer, _ = _consumer_with_scope(user)
    send = mock.AsyncMock()

    with (
        _auth_backend('auth_basic'),
        _lost_fan_out(),
        mock.patch.object(consumer, 'send', send),
    ):
        with django_capture_on_commit_callbacks(execute=True):
            user.last_login = timezone.now()
            user.save(update_fields=['last_login'])
        asyncio.run(consumer.asset_update({'asset_id': 'abc123'}))

    send.assert_awaited_once_with(text_data='abc123')


@pytest.mark.django_db
def test_deactivating_the_operator_revokes_authorization(
    django_capture_on_commit_callbacks: Any,
) -> None:
    """Fail closed on update_fields we haven't explicitly cleared:
    is_active decides who may hold a session just as much as the
    password does."""
    user = User.objects.create_user(username='alice', password='s3cret-pa55')
    consumer, _ = _consumer_with_scope(user)
    send = mock.AsyncMock()

    with (
        _auth_backend('auth_basic'),
        _lost_fan_out(),
        mock.patch.object(consumer, 'send', send),
    ):
        with django_capture_on_commit_callbacks(execute=True):
            user.is_active = False
            user.save(update_fields=['is_active'])
        asyncio.run(consumer.asset_update({'asset_id': 'abc123'}))

    send.assert_not_awaited()


@pytest.mark.django_db
def test_a_rolled_back_credential_change_does_not_revoke(
    django_capture_on_commit_callbacks: Any,
) -> None:
    """The receiver fires inside the caller's transaction, and Django's
    admin wraps its change form in one — so the write it reacts to can
    still roll back. A generation bump can't roll back with it, and a
    socket that also missed the close would then be silent for good
    over a credential change that never happened. Deferring to
    transaction.on_commit is what ties the revocation to durability."""
    user = User.objects.create_user(username='alice', password='s3cret-pa55')
    consumer, _ = _consumer_with_scope(user)
    send = mock.AsyncMock()

    with (
        _auth_backend('auth_basic'),
        _lost_fan_out(),
        mock.patch.object(consumer, 'send', send),
    ):
        # atomic() exits first and rolls back, then suppress() swallows
        # the error the way the admin's own error handling would.
        with (
            django_capture_on_commit_callbacks(execute=True) as callbacks,
            contextlib.suppress(RuntimeError),
            transaction.atomic(),
        ):
            user.set_password('a-rotated-pa55phrase')
            user.save(update_fields=['password'])
            raise RuntimeError('the admin view blew up')
        asyncio.run(consumer.asset_update({'asset_id': 'abc123'}))

    assert callbacks == []
    send.assert_awaited_once_with(text_data='abc123')
