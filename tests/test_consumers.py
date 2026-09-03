import asyncio
import contextlib
import itertools
from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest
import redis

from anthias_common import now_playing
from anthias_server.app import consumers
from anthias_server.app.consumers import AssetConsumer


@pytest.fixture(autouse=True)
def _reset_module_state() -> Iterator[None]:
    """The subscriber and its holder set are process-wide by design, so
    they have to be reset between tests or one test's open socket
    suppresses the next test's subscribe."""
    consumers._now_playing_watcher = None
    consumers._watchers_wanted.clear()
    consumers._warn.reset()
    yield
    consumers._now_playing_watcher = None
    consumers._watchers_wanted.clear()
    consumers._warn.reset()


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
    """Serves canned messages, then either idles or drops.

    Idling returns ``None`` the way a real poll does when nothing was
    published; dropping raises, which is how a lost subscription ends
    the task in production.
    """

    def __init__(
        self,
        messages: list[dict[str, object]],
        idle: bool = False,
    ) -> None:
        self._messages = list(messages)
        self._idle = idle
        self.subscribed_to: list[str] = []
        self.polls = 0

    async def subscribe(self, channel: str) -> None:
        self.subscribed_to.append(channel)

    async def get_message(
        self,
        ignore_subscribe_messages: bool = False,
        timeout: float | None = None,
    ) -> dict[str, object] | None:
        self.polls += 1
        if self._messages:
            return self._messages.pop(0)
        if self._idle:
            await asyncio.sleep(0)
            return None
        raise redis.ConnectionError('subscription dropped')


def _fake_redis(
    messages: list[dict[str, object]],
    idle: bool = False,
) -> tuple[mock.Mock, '_FakePubSub']:
    pubsub = _FakePubSub(messages, idle)
    client = mock.Mock()
    client.pubsub.return_value = pubsub
    client.aclose = mock.AsyncMock()
    return client, pubsub


def _fake_layer() -> mock.Mock:
    layer = mock.Mock()
    layer.group_send = mock.AsyncMock()
    return layer


def _watching(client: mock.Mock, layer: mock.Mock) -> Any:
    return (
        mock.patch(
            'anthias_server.app.consumers.get_channel_layer',
            return_value=layer,
        ),
        mock.patch(
            'anthias_server.app.consumers.connect_to_redis_async',
            return_value=client,
        ),
    )


def test_now_playing_watch_fans_out_through_the_channel_layer() -> None:
    """One subscription for the process, re-broadcast onto the group
    every socket is already in — rather than a send per socket from a
    task outside the consumer's dispatch loop."""
    client, pubsub = _fake_redis(
        [
            {'type': 'message', 'data': 'abc123'},
            {'type': 'message', 'data': ''},
        ]
    )
    layer = _fake_layer()
    layer_patch, redis_patch = _watching(client, layer)

    with layer_patch, redis_patch:
        asyncio.run(consumers._watch_now_playing())

    assert pubsub.subscribed_to == [now_playing.NOW_PLAYING_CHANNEL]
    assert layer.group_send.await_count == 2
    assert client.aclose.await_count == 1


def test_an_idle_poll_is_not_a_nudge() -> None:
    """The subscription reads with a timeout rather than blocking, so
    most polls return nothing. Those must not cost every open browser
    a table render."""
    client, pubsub = _fake_redis([], idle=True)
    layer = _fake_layer()
    layer_patch, redis_patch = _watching(client, layer)

    async def scenario() -> None:
        with layer_patch, redis_patch:
            task = asyncio.create_task(consumers._watch_now_playing())
            while pubsub.polls < 3:
                await asyncio.sleep(0)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())

    layer.group_send.assert_not_awaited()


def test_now_playing_watch_never_forwards_the_asset_id() -> None:
    """/ws is unauthenticated and not origin-gated under the default
    ALLOWED_HOSTS=['*'], and vendor.ts ignores the frame body anyway —
    so the id must not reach the wire. The generic '*' sentinel the
    write paths already send carries the same meaning."""
    client, _ = _fake_redis([{'type': 'message', 'data': 'secret-uuid'}])
    layer = _fake_layer()
    layer_patch, redis_patch = _watching(client, layer)

    with layer_patch, redis_patch:
        asyncio.run(consumers._watch_now_playing())

    (message,) = [c.args[1] for c in layer.group_send.await_args_list]
    assert message == {'type': 'asset_update', 'asset_id': '*'}
    assert 'secret-uuid' not in repr(layer.group_send.await_args_list)


def test_now_playing_watch_survives_an_unreachable_redis(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No Redis means no fast path, not a broken WebSocket — the
    browsers keep their 5s poll. Warned once rather than logged at
    DEBUG, so a genuine defect here (a redis-py API change) is visible
    in the journal at the default level instead of silently disabling
    the feature."""
    with (
        mock.patch(
            'anthias_server.app.consumers.get_channel_layer',
            return_value=_fake_layer(),
        ),
        mock.patch(
            'anthias_server.app.consumers.connect_to_redis_async',
            side_effect=OSError('no redis here'),
        ),
        caplog.at_level('DEBUG'),
    ):
        # Must not raise.
        asyncio.run(consumers._watch_now_playing())
        asyncio.run(consumers._watch_now_playing())

    warnings = [r for r in caplog.records if r.levelname == 'WARNING']
    assert len(warnings) == 1
    assert 'fall back to the 5s' in warnings[0].getMessage()


def test_now_playing_watch_without_a_channel_layer_is_a_no_op() -> None:
    """No CHANNEL_LAYERS configured — don't open a Redis connection
    only to have nowhere to send what comes back."""
    connect = mock.Mock()

    with (
        mock.patch(
            'anthias_server.app.consumers.get_channel_layer',
            return_value=None,
        ),
        mock.patch(
            'anthias_server.app.consumers.connect_to_redis_async', connect
        ),
    ):
        asyncio.run(consumers._watch_now_playing())

    connect.assert_not_called()


_channel_ids = itertools.count()


async def _quiesce(task: 'asyncio.Task[None]') -> None:
    """Cancel and await, so asyncio.run() doesn't close the loop on a
    pending task and log "Task was destroyed but it is pending!"."""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def _connected_consumer() -> AssetConsumer:
    consumer = AssetConsumer()
    consumer.channel_layer = mock.AsyncMock()
    consumer.channel_name = f'test-channel.{next(_channel_ids)}'
    return consumer


async def _open(consumer: AssetConsumer) -> None:
    with mock.patch.object(consumer, 'accept', mock.AsyncMock()):
        await consumer.connect()


def test_one_subscription_no_matter_how_many_tabs() -> None:
    """The point of the process-wide task: /ws has no auth and
    vendor.ts opens it on every page, so a subscription per socket
    would let anything that can reach the device pin a Redis
    connection per socket it opens."""
    client, _ = _fake_redis([], idle=True)
    connect = mock.Mock(return_value=client)
    layer_patch, _ = _watching(client, _fake_layer())

    async def scenario() -> None:
        tabs = [_connected_consumer() for _ in range(3)]
        with (
            layer_patch,
            mock.patch(
                'anthias_server.app.consumers.connect_to_redis_async', connect
            ),
        ):
            for tab in tabs:
                await _open(tab)
            await asyncio.sleep(0)

            assert connect.call_count == 1
            assert len(consumers._watchers_wanted) == 3

            # Two tabs close; the third still wants the push.
            for tab in tabs[:2]:
                await tab.disconnect(1000)
            watcher = consumers._now_playing_watcher
            assert watcher is not None and not watcher.done()

            await tabs[2].disconnect(1000)
            # Let the cancellation and the client close land.
            for _ in range(3):
                await asyncio.sleep(0)
            assert watcher.cancelled()
            client.aclose.assert_awaited_once()

    asyncio.run(scenario())


def test_disconnect_discards_the_channel_without_a_prior_connect() -> None:
    """disconnect() also runs for a socket that never completed
    connect(). The channel must still leave the group — every later
    notify_asset_update would fan out to a dead channel name — and a
    name that was never added must not strand the subscriber."""
    consumer = _connected_consumer()

    asyncio.run(consumer.disconnect(1006))

    consumer.channel_layer.group_discard.assert_awaited_once_with(
        'ws_server', consumer.channel_name
    )
    assert not consumers._watchers_wanted


def test_a_dead_subscriber_is_restarted_by_the_next_tab() -> None:
    """The body ends on any Redis failure. A server that outlives a
    Redis outage must get another attempt rather than staying
    poll-only until the container restarts."""
    dead, _ = _fake_redis([])  # drops as soon as it is polled
    alive, _ = _fake_redis([], idle=True)
    connect = mock.Mock(side_effect=[dead, alive])
    layer_patch, _ = _watching(dead, _fake_layer())

    async def scenario() -> None:
        with (
            layer_patch,
            mock.patch(
                'anthias_server.app.consumers.connect_to_redis_async', connect
            ),
        ):
            await _open(_connected_consumer())
            # Let the first subscriber drop and finish.
            for _ in range(5):
                await asyncio.sleep(0)
            assert consumers._now_playing_watcher is not None
            assert consumers._now_playing_watcher.done()

            await _open(_connected_consumer())
            await asyncio.sleep(0)

            assert connect.call_count == 2
            revived = consumers._now_playing_watcher
            assert revived is not None and not revived.done()
            await _quiesce(revived)

    asyncio.run(scenario())


def test_a_stopping_subscriber_is_not_handed_to_a_new_tab() -> None:
    """cancel() is a request, not an ending: a task asked to stop
    still reports done() as False for a moment. Reusing it would
    leave the browser that just arrived on poll-only for good."""
    first, _ = _fake_redis([], idle=True)
    second, _ = _fake_redis([], idle=True)
    connect = mock.Mock(side_effect=[first, second])
    layer_patch, _ = _watching(first, _fake_layer())

    async def scenario() -> None:
        with (
            layer_patch,
            mock.patch(
                'anthias_server.app.consumers.connect_to_redis_async', connect
            ),
        ):
            tab = _connected_consumer()
            await _open(tab)
            await asyncio.sleep(0)
            stopping = consumers._now_playing_watcher

            await tab.disconnect(1000)
            assert stopping is not None and not stopping.done()

            await _open(_connected_consumer())
            assert consumers._now_playing_watcher is not stopping
            await asyncio.sleep(0)
            assert connect.call_count == 2
            restarted = consumers._now_playing_watcher
            assert restarted is not None
            await _quiesce(restarted)

    asyncio.run(scenario())


def test_disconnect_releases_the_watcher_even_if_redis_is_gone() -> None:
    """group_discard raises when the channel layer's Redis is
    unreachable, and Channels lets that escape. If it skipped the
    release, the channel name would sit in the holder set for good and
    the subscription would outlive every socket that wanted it."""
    client, _ = _fake_redis([], idle=True)
    layer_patch, redis_patch = _watching(client, _fake_layer())

    async def scenario() -> None:
        with layer_patch, redis_patch:
            tab = _connected_consumer()
            await _open(tab)
            assert len(consumers._watchers_wanted) == 1

            tab.channel_layer.group_discard.side_effect = (
                redis.ConnectionError('channel layer is gone')
            )
            with pytest.raises(redis.ConnectionError):
                await tab.disconnect(1006)

            assert not consumers._watchers_wanted
            watcher = consumers._now_playing_watcher
            assert watcher is not None
            await _quiesce(watcher)
            assert watcher.cancelled()

    asyncio.run(scenario())
