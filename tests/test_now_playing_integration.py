"""End-to-end checks for the now-playing fact, against a real Redis.

The unit suites for this feature mock the Redis client wholesale, which
is the right call for the branch coverage but leaves one class of bug
uncovered: anything that depends on what redis-py actually does. Two of
those already bit during review.

- ``SET ... GET`` needs Redis >= 6.2. On an older server the write
  raises, the warn-once latch swallows it, and the highlight is dead
  for the life of the process with one line in the journal.
- The subscriber's read loop is coupled to redis-py's pub/sub API.
  Swapping ``listen()`` for ``get_message(timeout=...)`` broke every
  mocked test on a missing attribute rather than on behaviour, which
  is a test suite reporting on itself instead of on the code.

So these drive the real client and the real channel layer, and they are
marked ``integration`` because they need the Docker stack (``redis``
resolves there). They skip rather than fail anywhere else.
"""

import asyncio
import contextlib
import inspect
from collections.abc import Callable, Coroutine, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest import mock

import pytest
import redis
import redis.asyncio

from anthias_common import now_playing
from anthias_common.utils import connect_to_redis_async
from anthias_server.app import consumers

pytestmark = pytest.mark.integration

#: Long enough to absorb a loaded CI container, short enough that a
#: genuine hang fails the run rather than stalling it.
TIMEOUT_S = 15

#: The subscriber may not have issued its SUBSCRIBE when the first
#: publish goes out, and Redis drops a message with no subscriber. So
#: publish on a tick until the fan-out lands.
PUBLISH_EVERY_S = 0.2

#: Long enough that the subscriber has certainly been through the
#: no-message path; _SUBSCRIPTION_POLL_S itself is a ceiling on one
#: read, so waiting a whole one of those would only slow the suite.
IDLE_STRETCH_S = 2.0


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine on a private loop in its own thread.

    Not ``asyncio.run()`` on this thread, and not pytest-asyncio,
    anyio's plugin or ``asgiref.async_to_sync`` either: the Playwright
    sync API keeps a loop running on the thread pytest calls tests on,
    and all of those want to drive a loop there too. CI runs the whole
    integration suite in one process, so by the time these run a
    browser test has already started one. ``Future.result()`` re-raises
    with the original traceback, so a failure still reports as one.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


@pytest.fixture
def client() -> Iterator[Any]:
    """A real sync client, or a skip.

    The root conftest replaces :func:`connect_to_redis` process-wide,
    so this builds its own rather than taking the fake. Typed ``Any``
    to match how :mod:`anthias_common.now_playing` types the client it
    is handed.
    """
    real: Any = redis.Redis(
        host='redis',
        port=6379,
        db=0,
        decode_responses=True,
        socket_connect_timeout=2,
    )
    try:
        real.ping()
    except redis.RedisError as exc:
        pytest.skip(f'no Redis at redis:6379 ({exc})')
    real.delete(now_playing.NOW_PLAYING_KEY)
    yield real
    real.delete(now_playing.NOW_PLAYING_KEY)
    real.close()


@pytest.fixture(autouse=True)
def _reset_module_state() -> Iterator[None]:
    now_playing._believed = None
    now_playing._last_announced_at = None
    now_playing._latch.reset()
    consumers._warn.reset()
    consumers._now_playing_watcher = None
    consumers._watchers_wanted.clear()
    yield
    now_playing._believed = None
    now_playing._last_announced_at = None


def test_publish_and_read_round_trip_on_a_real_server(
    client: Any,
) -> None:
    """``SET ... GET`` is a Redis 6.2 feature and the write path is
    latched, so a server too old to support it would look like "the
    viewer hasn't reported yet" rather than an error."""
    now_playing.publish(client, 'asset-under-test')

    assert now_playing.read(client) == 'asset-under-test'
    ttl = client.ttl(now_playing.NOW_PLAYING_KEY)
    assert 0 < ttl <= now_playing.TTL_S

    now_playing.clear(client)
    assert now_playing.read(client) is None


def test_refresh_restores_the_fact_after_a_flush(
    client: Any,
) -> None:
    """The reporter re-asserts what this process displayed rather than
    EXPIREing the key, which is what lets the highlight come back by
    itself when Redis is restarted under a running viewer."""
    now_playing.publish(client, 'asset-under-test')
    client.delete(now_playing.NOW_PLAYING_KEY)
    assert now_playing.read(client) is None

    now_playing.refresh(client)

    assert now_playing.read(client) == 'asset-under-test'


def test_a_publish_reaches_the_websocket_group(client: Any) -> None:
    """The whole bridge, with nothing mocked: a viewer-side publish on
    the pub/sub channel comes out as an asset_update on the group every
    open socket belongs to.

    This is the test that pins the code to redis-py's real pub/sub API
    and to the channel layer's real message shape.
    """
    from channels.layers import get_channel_layer

    async def scenario() -> dict[str, Any]:
        layer = get_channel_layer()
        assert layer is not None, 'CHANNEL_LAYERS is not configured'
        channel = await layer.new_channel()
        await layer.group_add(consumers.WS_GROUP, channel)

        watcher = asyncio.create_task(consumers._watch_now_playing())
        try:
            received = asyncio.create_task(layer.receive(channel))
            deadline = asyncio.get_running_loop().time() + TIMEOUT_S
            while not received.done():
                if asyncio.get_running_loop().time() > deadline:
                    raise AssertionError(
                        'the publish never reached the group; '
                        f'watcher done={watcher.done()}'
                    )
                client.publish(
                    now_playing.NOW_PLAYING_CHANNEL, 'asset-under-test'
                )
                await asyncio.sleep(PUBLISH_EVERY_S)
            message: dict[str, Any] = await received
            return message
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            await layer.group_discard(consumers.WS_GROUP, channel)

    message = _run(scenario())

    assert message['type'] == 'asset_update'
    # Not the asset_id: /ws has no auth, so the bridge sends the write
    # paths' generic sentinel rather than what is on screen.
    assert message['asset_id'] == '*'


def test_the_subscription_survives_an_idle_stretch(
    client: Any,
) -> None:
    """The read loop polls with a timeout rather than blocking, so it
    has to keep working across polls that return nothing. A blocking
    ``listen()`` would pass this too; what it pins down is that the
    timeout path doesn't end the task.
    """
    from channels.layers import get_channel_layer

    async def scenario() -> bool:
        layer = get_channel_layer()
        assert layer is not None
        channel = await layer.new_channel()
        await layer.group_add(consumers.WS_GROUP, channel)

        watcher = asyncio.create_task(consumers._watch_now_playing())
        try:
            # Several times the poll interval with nothing published.
            await asyncio.sleep(IDLE_STRETCH_S)
            if watcher.done():
                return False

            received = asyncio.create_task(layer.receive(channel))
            deadline = asyncio.get_running_loop().time() + TIMEOUT_S
            while not received.done():
                if asyncio.get_running_loop().time() > deadline:
                    received.cancel()
                    return False
                client.publish(now_playing.NOW_PLAYING_CHANNEL, 'later')
                await asyncio.sleep(PUBLISH_EVERY_S)
            await received
            return True
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            await layer.group_discard(consumers.WS_GROUP, channel)

    assert _run(scenario()), (
        'the subscription stopped delivering after an idle stretch'
    )


class _Relay:
    """A TCP hop between the subscriber and Redis that a test can cut.

    ``CLIENT KILL`` is not enough to stand in for an outage: redis-py
    reconnects and re-SUBSCRIBEs from inside ``get_message``, so an
    instant drop against a live server never reaches the task at all.
    What SIRI-61 describes is a server that is *gone* for longer than
    that retry budget — ``docker compose restart redis`` — and a test
    cannot restart the container it is running against. So it puts a
    hop in the path and closes that instead: connections in flight
    die and new ones are refused, which is what the subscriber sees
    either way.
    """

    def __init__(self) -> None:
        self.port = 0
        self._server: asyncio.AbstractServer | None = None
        self._sessions: set[asyncio.Future[Any]] = set()

    async def open(self) -> None:
        """Listen, reusing the same port across a cut so the client
        under test keeps one address for the whole scenario."""
        self._server = await asyncio.start_server(
            self._handle, '127.0.0.1', self.port
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def cut(self) -> None:
        """Stop accepting, and drop what is already connected.

        Deliberately not ``wait_closed()``: since 3.12.1 that waits
        for the handlers, and the handlers are the sessions being
        torn down here — waiting first is a deadlock. ``close()``
        releases the listening socket on its own, which is what the
        next ``open()`` needs.
        """
        if self._server is not None:
            self._server.close()
            self._server = None
        sessions, self._sessions = self._sessions, set()
        for session in sessions:
            session.cancel()
        await asyncio.gather(*sessions, return_exceptions=True)

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            upstream_r, upstream_w = await asyncio.open_connection(
                'redis', 6379
            )
        except OSError:
            writer.close()
            return
        session = asyncio.gather(
            self._pump(reader, upstream_w),
            self._pump(upstream_r, writer),
        )
        self._sessions.add(session)
        try:
            await session
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            self._sessions.discard(session)
            writer.close()
            upstream_w.close()

    @staticmethod
    async def _pump(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()


#: Longer than redis-py's own reconnect budget, so the failure reaches
#: the task rather than being absorbed under it — that is the whole
#: point of the scenario. Kept short so the suite doesn't pay for it.
OUTAGE_S = 4.0


def _via(port: int) -> 'Callable[[], Any]':
    """The shipped client, dialled through the relay.

    Built from ``connect_to_redis_async``'s own connection kwargs
    rather than a second copy of them, so the timeouts and the retry
    policy under test stay the ones that ship; the address is the only
    thing redirected. The pool records more than the constructor
    takes (defaults it filled in itself), so the derived set is
    narrowed to what ``Redis`` actually accepts.
    """
    accepted = inspect.signature(redis.asyncio.Redis).parameters
    template = connect_to_redis_async()
    kwargs = {
        name: value
        for name, value in template.connection_pool.connection_kwargs.items()
        if name in accepted
    }
    kwargs.update(host='127.0.0.1', port=port)

    def build() -> Any:
        return redis.asyncio.Redis(**kwargs)

    return build


def test_the_subscription_comes_back_after_a_redis_outage(
    client: Any,
) -> None:
    """SIRI-61: the subscriber re-establishes itself, with no new
    WebSocket anywhere in the scenario.

    That is the whole point. The browser's socket terminates at
    uvicorn rather than at Redis, so an outage never closes it and
    ``vendor.ts`` never reconnects — nothing fires the
    restart-on-connect path, and before this every already-open tab
    sat on the 5s poll until someone reloaded it.

    The recovery is read off the server (``PUBSUB CHANNELS``) and off
    a message actually arriving, not off a mock's call count.
    """
    from channels.layers import get_channel_layer

    def subscribers() -> int:
        return len(
            client.execute_command(
                'PUBSUB', 'CHANNELS', now_playing.NOW_PLAYING_CHANNEL
            )
        )

    async def settle(want: int, what: str) -> None:
        deadline = asyncio.get_running_loop().time() + TIMEOUT_S
        while subscribers() != want:
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f'timed out waiting for {what}')
            await asyncio.sleep(0.1)

    async def scenario() -> None:
        layer = get_channel_layer()
        assert layer is not None, 'CHANNEL_LAYERS is not configured'
        relay = _Relay()
        await relay.open()

        with mock.patch(
            'anthias_server.app.consumers.connect_to_redis_async',
            _via(relay.port),
        ):
            watcher = asyncio.create_task(consumers._watch_now_playing())
            try:
                await settle(1, 'the first SUBSCRIBE')

                await relay.cut()
                await settle(0, 'the subscriber to go with the server')
                await asyncio.sleep(OUTAGE_S)
                assert not watcher.done(), (
                    'the subscriber gave up on the outage; every open tab '
                    'is poll-only until the page is reloaded'
                )

                await relay.open()
                await settle(1, 'the subscription to come back by itself')

                # Joined only now, so nothing published before the
                # outage can be waiting in it: a message arriving here
                # came through the subscription that was rebuilt.
                channel = await layer.new_channel()
                await layer.group_add(consumers.WS_GROUP, channel)
                try:
                    received = asyncio.create_task(layer.receive(channel))
                    deadline = asyncio.get_running_loop().time() + TIMEOUT_S
                    while not received.done():
                        if asyncio.get_running_loop().time() > deadline:
                            received.cancel()
                            raise AssertionError(
                                'the rebuilt subscription is not forwarding'
                            )
                        client.publish(
                            now_playing.NOW_PLAYING_CHANNEL, 'after-the-outage'
                        )
                        await asyncio.sleep(PUBLISH_EVERY_S)
                    assert (await received)['type'] == 'asset_update'
                finally:
                    await layer.group_discard(consumers.WS_GROUP, channel)
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
                with contextlib.suppress(AssertionError):
                    await relay.cut()

    _run(scenario())
