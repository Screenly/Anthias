import asyncio
from typing import Any
from unittest import mock

import pytest

from anthias_common import now_playing
from anthias_server.app.consumers import AssetConsumer


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
# Now-playing subscription — issue #3177
# ---------------------------------------------------------------------------
#
# The table's 5s poll would surface a rotation eventually; subscribing
# means the highlight lands with the picture instead of up to 5s after
# it, which is what makes stepping through assets with Next feel
# connected to the screen. Best-effort throughout: every failure path
# falls back to that poll rather than breaking the socket.


class _FakePubSub:
    def __init__(
        self,
        messages: list[dict[str, object]],
        listener: Any = None,
    ) -> None:
        self._messages = messages
        self.subscribed_to: list[str] = []
        if listener is not None:
            self.listen = listener  # type: ignore[method-assign]

    async def subscribe(self, channel: str) -> None:
        self.subscribed_to.append(channel)

    async def listen(self):  # type: ignore[no-untyped-def]
        for message in self._messages:
            yield message


def _fake_redis(
    messages: list[dict[str, object]],
    listener: Any = None,
) -> tuple[mock.Mock, '_FakePubSub']:
    pubsub = _FakePubSub(messages, listener)
    client = mock.Mock()
    client.pubsub.return_value = pubsub
    client.aclose = mock.AsyncMock()
    return client, pubsub


def test_now_playing_watch_nudges_the_browser() -> None:
    client, pubsub = _fake_redis(
        [
            {'type': 'message', 'data': 'abc123'},
            {'type': 'message', 'data': ''},
        ]
    )
    consumer = AssetConsumer()
    send = mock.AsyncMock()

    with (
        mock.patch.object(consumer, 'send', send),
        mock.patch(
            'anthias_server.app.consumers.connect_to_redis_async',
            return_value=client,
        ),
    ):
        asyncio.run(consumer._watch_now_playing())

    assert pubsub.subscribed_to == [now_playing.NOW_PLAYING_CHANNEL]
    assert [c.kwargs['text_data'] for c in send.await_args_list] == [
        'abc123',
        '',
    ]
    assert client.aclose.await_count == 1


def test_now_playing_watch_survives_an_unreachable_redis() -> None:
    """No Redis means no fast path, not a broken WebSocket — the
    browser keeps its 5s poll and the socket stays up."""
    consumer = AssetConsumer()

    with (
        mock.patch.object(consumer, 'send', mock.AsyncMock()),
        mock.patch(
            'anthias_server.app.consumers.connect_to_redis_async',
            side_effect=OSError('no redis here'),
        ),
    ):
        # Must not raise.
        asyncio.run(consumer._watch_now_playing())


def test_now_playing_watch_stops_when_the_browser_goes_away() -> None:
    """A send on a closed socket raises the same send-after-close
    RuntimeError asset_update guards against (ANTHIAS-1K); the task
    must end quietly instead of surfacing it."""
    client, _ = _fake_redis([{'type': 'message', 'data': 'abc123'}])
    consumer = AssetConsumer()
    send = mock.AsyncMock(
        side_effect=RuntimeError(
            "Unexpected ASGI message 'websocket.send', after sending "
            "'websocket.close' or response already completed."
        )
    )

    with (
        mock.patch.object(consumer, 'send', send),
        mock.patch(
            'anthias_server.app.consumers.connect_to_redis_async',
            return_value=client,
        ),
    ):
        asyncio.run(consumer._watch_now_playing())

    send.assert_awaited_once()


def test_connect_subscribes_and_disconnect_closes_the_connection() -> None:
    """The subscription is tied to the socket: every closed tab must
    take its Redis connection with it, or a device left on a dashboard
    accumulates them. Drives the real _watch_now_playing — a stand-in
    with no cleanup would pass this while the real body leaked."""
    blocked = asyncio.Event()

    async def listen():  # type: ignore[no-untyped-def]
        # Hold the subscription open the way a live pubsub does.
        yield {'type': 'message', 'data': 'abc123'}
        await blocked.wait()

    client, _ = _fake_redis([], listener=listen)
    consumer = AssetConsumer()
    consumer.channel_layer = mock.AsyncMock()
    consumer.channel_name = 'test-channel'
    sent = asyncio.Event()

    async def send(text_data=None):  # type: ignore[no-untyped-def]
        sent.set()

    async def scenario() -> None:
        with (
            mock.patch.object(consumer, 'accept', mock.AsyncMock()),
            mock.patch.object(consumer, 'send', send),
            mock.patch(
                'anthias_server.app.consumers.connect_to_redis_async',
                return_value=client,
            ),
        ):
            await consumer.connect()
            await asyncio.wait_for(sent.wait(), timeout=2)
            task = consumer._now_playing_task
            assert not task.done()

            await consumer.disconnect(1000)
            assert task.done()
            client.aclose.assert_awaited_once()

    asyncio.run(scenario())


def test_now_playing_watch_ignores_non_text_frames() -> None:
    """redis-py surfaces subscribe confirmations and binary payloads
    through the same iterator; only a text frame is a nudge."""
    client, _ = _fake_redis(
        [
            {'type': 'subscribe', 'data': 1},
            {'type': 'message', 'data': b'\x00binary'},
            {'type': 'message', 'data': 'abc123'},
        ]
    )
    consumer = AssetConsumer()
    send = mock.AsyncMock()

    with (
        mock.patch.object(consumer, 'send', send),
        mock.patch(
            'anthias_server.app.consumers.connect_to_redis_async',
            return_value=client,
        ),
    ):
        asyncio.run(consumer._watch_now_playing())

    assert [c.kwargs['text_data'] for c in send.await_args_list] == ['abc123']


def test_disconnect_without_a_watcher_is_harmless() -> None:
    """disconnect() also runs for a socket that never completed
    connect(), where no task was ever created."""
    consumer = AssetConsumer()
    consumer.channel_layer = mock.AsyncMock()
    consumer.channel_name = 'test-channel'

    # Must not raise.
    asyncio.run(consumer.disconnect(1006))
