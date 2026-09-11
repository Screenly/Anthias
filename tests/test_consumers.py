import asyncio
import contextlib
import itertools
from collections.abc import Callable, Iterator
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
    an attempt in production.
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


async def _quiesce(task: 'asyncio.Task[None]') -> None:
    """Cancel and await, so asyncio.run() doesn't close the loop on a
    pending task and log "Task was destroyed but it is pending!"."""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _until(
    predicate: Callable[[], bool], what: str, passes: int = 500
) -> None:
    """Hand the loop back until ``predicate`` holds.

    The watcher is a task now rather than a coroutine these tests can
    await to completion — it only ends on cancellation — so they drive
    it to the point of interest and stop it there. A pass budget
    rather than a wall-clock deadline, because the backoff waits are
    patched out: a scenario that has stopped making progress has
    stopped for good, and failing on the spot beats hanging CI.
    """
    for _ in range(passes):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError(f'timed out waiting for {what}')


@contextlib.contextmanager
def _recorded_backoff() -> Iterator[list[float]]:
    """Take the retry loop's waits instantly, recording what it asked
    for.

    Patching ``asyncio.sleep`` wholesale rather than cutting a seam
    into the module for tests: the loop's own waits are the only
    non-zero ones in these scenarios, and this keeps the assertions on
    the shipped code path. ``_FakePubSub`` idles with ``sleep(0)``,
    which is not a backoff and is left out.
    """
    real_sleep = asyncio.sleep
    delays: list[float] = []

    async def instant(delay: float, *args: Any, **kwargs: Any) -> Any:
        if delay:
            delays.append(delay)
        return await real_sleep(0)

    with mock.patch.object(asyncio, 'sleep', instant):
        yield delays


def test_now_playing_watch_fans_out_through_the_channel_layer() -> None:
    """One subscription for the process, re-broadcast onto the group
    every socket is already in — rather than a send per socket from a
    task outside the consumer's dispatch loop."""
    client, pubsub = _fake_redis(
        [
            {'type': 'message', 'data': 'abc123'},
            {'type': 'message', 'data': ''},
        ],
        idle=True,
    )
    layer = _fake_layer()
    layer_patch, redis_patch = _watching(client, layer)

    async def scenario() -> None:
        with layer_patch, redis_patch:
            task = asyncio.create_task(consumers._watch_now_playing())
            await _until(
                lambda: layer.group_send.await_count == 2,
                'both messages to reach the group',
            )
            await _quiesce(task)

    asyncio.run(scenario())

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
    client, _ = _fake_redis(
        [{'type': 'message', 'data': 'secret-uuid'}], idle=True
    )
    layer = _fake_layer()
    layer_patch, redis_patch = _watching(client, layer)

    async def scenario() -> None:
        with layer_patch, redis_patch:
            task = asyncio.create_task(consumers._watch_now_playing())
            await _until(
                lambda: layer.group_send.await_count == 1,
                'the message to reach the group',
            )
            await _quiesce(task)

    asyncio.run(scenario())

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
    the feature — and once for the whole outage, not once per retry."""
    connect = mock.Mock(side_effect=OSError('no redis here'))

    async def scenario() -> None:
        with (
            mock.patch(
                'anthias_server.app.consumers.get_channel_layer',
                return_value=_fake_layer(),
            ),
            mock.patch(
                'anthias_server.app.consumers.connect_to_redis_async',
                connect,
            ),
            _recorded_backoff(),
        ):
            # Must not raise.
            task = asyncio.create_task(consumers._watch_now_playing())
            await _until(lambda: connect.call_count >= 4, 'four dials')
            await _quiesce(task)

    with caplog.at_level('DEBUG'):
        asyncio.run(scenario())

    warnings = [r for r in caplog.records if r.levelname == 'WARNING']
    assert len(warnings) == 1
    assert 'fall back to the 5s' in warnings[0].getMessage()


def test_the_subscriber_re_establishes_itself_after_a_drop() -> None:
    """The fix for SIRI-61. The browser's socket terminates at uvicorn,
    not at Redis, so a Redis blip never closes it and vendor.ts never
    reconnects — the restart-on-connect path is uncorrelated with Redis
    coming back. Every already-open tab used to stay poll-only until
    someone reloaded the page; the task has to reconnect on its own,
    with no new socket in the scenario at all."""
    dropped, _ = _fake_redis([])  # raises as soon as it is polled
    revived, _ = _fake_redis([{'type': 'message', 'data': 'back'}], idle=True)
    connect = mock.Mock(side_effect=[dropped, revived])
    layer = _fake_layer()
    layer_patch, _ = _watching(dropped, layer)

    async def scenario() -> None:
        with (
            layer_patch,
            mock.patch(
                'anthias_server.app.consumers.connect_to_redis_async', connect
            ),
            _recorded_backoff(),
        ):
            task = asyncio.create_task(consumers._watch_now_playing())
            await _until(
                lambda: layer.group_send.await_count == 1,
                'the push to come back by itself',
            )
            assert not task.done()
            await _quiesce(task)

    asyncio.run(scenario())

    assert connect.call_count == 2
    # The dropped attempt's client is closed rather than leaked, so a
    # long outage does not accumulate one connection per retry.
    assert dropped.aclose.await_count == 1


def test_the_retry_backs_off_and_is_capped() -> None:
    """A Redis that is down for the night must not cost a dial a
    second. The waits double from the floor and stop at the ceiling."""
    connect = mock.Mock(side_effect=OSError('no redis here'))

    async def scenario() -> list[float]:
        with (
            mock.patch(
                'anthias_server.app.consumers.get_channel_layer',
                return_value=_fake_layer(),
            ),
            mock.patch(
                'anthias_server.app.consumers.connect_to_redis_async',
                connect,
            ),
            _recorded_backoff() as delays,
        ):
            task = asyncio.create_task(consumers._watch_now_playing())
            await _until(lambda: len(delays) >= 12, 'twelve retries')
            await _quiesce(task)
            return list(delays)

    delays = asyncio.run(scenario())

    floor = consumers._RETRY_MIN_S
    assert delays[:4] == [floor, floor * 2, floor * 4, floor * 8]
    assert max(delays) == consumers._RETRY_MAX_S
    assert delays[-1] == consumers._RETRY_MAX_S


def test_a_flapping_redis_is_one_warning_not_one_per_cycle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A server that accepts SUBSCRIBE and drops the connection a
    second later is the same fault continuing, not a recovery. If the
    latch re-armed on every successful subscribe, that would be a
    WARNING per cycle — the journal-flooding warn_once exists to
    prevent (#3268)."""
    flapping = [_fake_redis([])[0] for _ in range(6)]
    connect = mock.Mock(side_effect=flapping)
    layer_patch, _ = _watching(flapping[0], _fake_layer())

    async def scenario() -> None:
        with (
            layer_patch,
            mock.patch(
                'anthias_server.app.consumers.connect_to_redis_async', connect
            ),
            _recorded_backoff(),
        ):
            task = asyncio.create_task(consumers._watch_now_playing())
            await _until(lambda: connect.call_count >= 5, 'five flaps')
            await _quiesce(task)

    with caplog.at_level('DEBUG'):
        asyncio.run(scenario())

    warnings = [r for r in caplog.records if r.levelname == 'WARNING']
    assert len(warnings) == 1


def test_a_recovered_subscription_re_arms_the_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other side of the latch: once an attempt has held long
    enough to count as healthy, a later outage is news again rather
    than being filed at DEBUG for the life of the process.

    _STABLE_AFTER_S is patched to zero so the first poll of each
    attempt clears the mark; at its real value these scenarios run in
    well under a minute and would never reach it."""
    served, _ = _fake_redis([{'type': 'message', 'data': 'x'}])
    later, _ = _fake_redis([{'type': 'message', 'data': 'y'}])
    connect = mock.Mock(
        side_effect=[
            served,
            later,
            *(_fake_redis([], idle=True)[0] for _ in range(2)),
        ]
    )
    layer = _fake_layer()
    layer_patch, _ = _watching(served, layer)

    async def scenario() -> None:
        with (
            layer_patch,
            mock.patch(
                'anthias_server.app.consumers.connect_to_redis_async', connect
            ),
            mock.patch.object(consumers, '_STABLE_AFTER_S', 0.0),
            _recorded_backoff(),
        ):
            task = asyncio.create_task(consumers._watch_now_playing())
            await _until(lambda: connect.call_count >= 3, 'two outages')
            await _quiesce(task)

    with caplog.at_level('DEBUG'):
        asyncio.run(scenario())

    warnings = [r for r in caplog.records if r.levelname == 'WARNING']
    assert len(warnings) == 2


def test_cancelling_during_the_backoff_stops_the_task() -> None:
    """_release_now_playing_watcher cancels when the last tab closes,
    and that can land while the loop is waiting out a retry. The task
    must stop there rather than sit out the wait and dial again on a
    device nobody is looking at."""
    connect = mock.Mock(side_effect=OSError('no redis here'))
    waiting = asyncio.Event()

    async def scenario() -> 'asyncio.Task[None]':
        real_sleep = asyncio.sleep

        async def block(delay: float, *args: Any, **kwargs: Any) -> Any:
            if delay:
                waiting.set()
                # Long enough that the task is certainly still in the
                # wait when the cancellation arrives.
                return await real_sleep(30)
            return await real_sleep(0)

        with (
            mock.patch(
                'anthias_server.app.consumers.get_channel_layer',
                return_value=_fake_layer(),
            ),
            mock.patch(
                'anthias_server.app.consumers.connect_to_redis_async',
                connect,
            ),
            mock.patch.object(asyncio, 'sleep', block),
        ):
            task = asyncio.create_task(consumers._watch_now_playing())
            # Bounded, so a regression that skips the wait entirely
            # fails the run rather than hanging it — the timer is the
            # loop's own, not the patched-out sleep.
            await asyncio.wait_for(waiting.wait(), timeout=10)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            return task

    task = asyncio.run(scenario())

    assert task.cancelled()
    assert connect.call_count == 1


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


def test_a_finished_subscriber_is_restarted_by_the_next_tab() -> None:
    """The body retries a dropped subscription itself now, so this is
    a backstop rather than the recovery path: whatever ended the task
    — no channel layer at the time it started, a BaseException the
    retry loop deliberately does not catch — the next socket must get
    a live subscriber rather than be handed a finished task."""
    alive, _ = _fake_redis([], idle=True)
    connect = mock.Mock(return_value=alive)
    layer_patch, _ = _watching(alive, _fake_layer())

    async def scenario() -> None:
        finished = asyncio.create_task(asyncio.sleep(0))
        await finished
        consumers._now_playing_watcher = finished

        with (
            layer_patch,
            mock.patch(
                'anthias_server.app.consumers.connect_to_redis_async', connect
            ),
        ):
            await _open(_connected_consumer())
            await asyncio.sleep(0)

            assert connect.call_count == 1
            revived = consumers._now_playing_watcher
            assert revived is not finished
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
