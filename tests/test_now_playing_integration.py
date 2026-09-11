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
from collections.abc import Coroutine, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
import redis

from anthias_common import now_playing
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
