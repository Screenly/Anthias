import json
import logging
from typing import Any, cast
from unittest import mock
from unittest.mock import MagicMock

import pytest
import redis

from anthias_common.errors import ReplyTimeoutError
from anthias_server import settings as settings_module
from anthias_server.settings import (
    REPLY_KEY_PREFIX,
    ReplyCollector,
    ReplySender,
    ViewerPublisher,
)
from anthias_viewer.messaging import ViewerSubscriber

logging.disable(logging.CRITICAL)


# ---------------------------------------------------------------------------
# settings.ViewerPublisher / ReplySender / ReplyCollector
# ---------------------------------------------------------------------------


def test_viewer_publisher_send_publishes_correct_payload() -> None:
    fake_redis = MagicMock()
    publisher = ViewerPublisher.__new__(ViewerPublisher)
    publisher._redis = fake_redis

    publisher.send_to_viewer('next')
    fake_redis.publish.assert_called_once_with(
        settings_module.VIEWER_CHANNEL, 'viewer next'
    )


def test_viewer_publisher_singleton_rejects_second_init() -> None:
    sentinel = cast(ViewerPublisher, object())
    original = ViewerPublisher.INSTANCE
    try:
        ViewerPublisher.INSTANCE = sentinel
        with pytest.raises(ValueError, match='instance already exists'):
            ViewerPublisher()
    finally:
        ViewerPublisher.INSTANCE = original


def test_viewer_publisher_get_instance_creates_once() -> None:
    original = ViewerPublisher.INSTANCE
    ViewerPublisher.INSTANCE = None
    try:
        # connect_to_redis is called inside __init__; mock it.
        with mock.patch(
            'anthias_common.utils.connect_to_redis', return_value=MagicMock()
        ):
            inst = ViewerPublisher.get_instance()
        assert inst is ViewerPublisher.INSTANCE
        # Calling get_instance again returns the cached instance.
        with mock.patch(
            'anthias_common.utils.connect_to_redis', return_value=MagicMock()
        ):
            assert ViewerPublisher.get_instance() is inst
    finally:
        ViewerPublisher.INSTANCE = original


def test_reply_sender_pushes_json_and_sets_ttl() -> None:
    fake_redis = MagicMock()
    sender = ReplySender(fake_redis)
    sender.send('correlation-1', {'asset_id': 'abc'})

    expected_key = f'{REPLY_KEY_PREFIX}correlation-1'
    fake_redis.rpush.assert_called_once_with(
        expected_key, json.dumps({'asset_id': 'abc'})
    )
    fake_redis.expire.assert_called_once_with(expected_key, 30)


def _collector(fake_redis: MagicMock) -> ReplyCollector:
    """A collector whose every connection is ``fake_redis``.

    Both the shared connection (non-blocking LPOP) and the per-budget
    blocking ones resolve to the same mock, so a test can assert on
    ``lpop``/``blpop`` without caring which pool served it.
    """
    collector = ReplyCollector.__new__(ReplyCollector)
    collector._redis = fake_redis
    collector._blocking_redis = {}
    collector._blocking_connection = (  # type: ignore[method-assign]
        lambda timeout_seconds: fake_redis
    )
    return collector


def test_reply_collector_recv_blocking_returns_decoded_payload() -> None:
    fake_redis = MagicMock()
    fake_redis.blpop.return_value = (
        f'{REPLY_KEY_PREFIX}cid'.encode(),
        json.dumps({'ok': True}),
    )
    collector = _collector(fake_redis)

    result = collector.recv_json('cid', timeout_ms=2000)
    assert result == {'ok': True}
    fake_redis.blpop.assert_called_once_with(
        f'{REPLY_KEY_PREFIX}cid', timeout=2
    )


def test_reply_collector_blocking_rounds_up_to_next_second() -> None:
    fake_redis = MagicMock()
    fake_redis.blpop.return_value = (b'k', json.dumps(1))
    collector = _collector(fake_redis)

    collector.recv_json('cid', timeout_ms=1500)
    # 1500ms → 2 seconds.
    fake_redis.blpop.assert_called_once_with(
        f'{REPLY_KEY_PREFIX}cid', timeout=2
    )


def test_reply_collector_blocking_timeout() -> None:
    fake_redis = MagicMock()
    fake_redis.blpop.return_value = None
    collector = _collector(fake_redis)

    with pytest.raises(ReplyTimeoutError):
        collector.recv_json('cid', timeout_ms=100)


def test_blocking_pop_socket_read_outlives_the_pop() -> None:
    """The socket read must outlast the BLPOP it carries.

    On redis-py's defaults (``socket_timeout=5``,
    ``Retry(retries=10)``) a 15s pop does not wait 15s and then come
    back empty: the socket read gives up after 5, the ladder re-issues
    the pop ten more times, and the call raises ``redis.TimeoutError``
    about a minute later. Measured at 58.0s for ``cec_client``'s 15s
    budget, which is how ``get_display_power`` reached its 60s hard
    limit and had its pool child SIGKILLed.
    """
    created: list[dict[str, Any]] = []

    def fake_connect(**overrides: Any) -> MagicMock:
        created.append(overrides)
        return MagicMock()

    collector = ReplyCollector.__new__(ReplyCollector)
    collector._redis = MagicMock()
    collector._blocking_redis = {}

    with mock.patch(
        'anthias_common.utils.connect_to_redis', side_effect=fake_connect
    ):
        collector._blocking_connection(15)

    assert len(created) == 1
    overrides = created[0]
    assert overrides['socket_timeout'] > 15, (
        'the socket read gives up before the pop does, so the pop is '
        'abandoned rather than returning empty'
    )
    # Re-issuing a blocking pop restarts its wait from zero, which is
    # what turns one bounded request into a minute-long stall.
    assert overrides['retry_on_timeout'] is False
    assert overrides['retry'].get_retries() == 0


def test_blocking_connections_are_cached_per_budget() -> None:
    """One pool per distinct budget, not one per pop."""
    collector = ReplyCollector.__new__(ReplyCollector)
    collector._redis = MagicMock()
    collector._blocking_redis = {}

    with mock.patch(
        'anthias_common.utils.connect_to_redis',
        side_effect=lambda **kwargs: MagicMock(),
    ) as connect:
        first = collector._blocking_connection(15)
        again = collector._blocking_connection(15)
        other = collector._blocking_connection(2)

    assert first is again
    assert other is not first
    assert connect.call_count == 2


def test_cec_reply_budget_is_carried_by_the_blocking_connection() -> None:
    """``cec_client``'s budget must be one the transport can carry.

    Ties the constant to the fix: if the CEC budget grows past what
    ``_blocking_connection`` sizes for, the pop silently falls back
    into the retry ladder and the ANTHIAS-A/9/B/1Q chain returns.
    """
    from anthias_server.lib import cec_client

    budget_seconds = (cec_client.REPLY_TIMEOUT_MS + 999) // 1000

    collector = ReplyCollector.__new__(ReplyCollector)
    collector._redis = MagicMock()
    collector._blocking_redis = {}

    with mock.patch(
        'anthias_common.utils.connect_to_redis',
        side_effect=lambda **kwargs: MagicMock(),
    ) as connect:
        collector._blocking_connection(budget_seconds)

    assert connect.call_args.kwargs['socket_timeout'] > budget_seconds


def test_reply_collector_non_blocking_uses_lpop() -> None:
    fake_redis = MagicMock()
    fake_redis.lpop.return_value = json.dumps({'value': 42})
    collector = ReplyCollector.__new__(ReplyCollector)
    collector._redis = fake_redis

    assert collector.recv_json('cid', timeout_ms=0) == {'value': 42}
    fake_redis.lpop.assert_called_once_with(f'{REPLY_KEY_PREFIX}cid')
    fake_redis.blpop.assert_not_called()


def test_reply_collector_non_blocking_negative_timeout_uses_lpop() -> None:
    fake_redis = MagicMock()
    fake_redis.lpop.return_value = json.dumps([1, 2, 3])
    collector = ReplyCollector.__new__(ReplyCollector)
    collector._redis = fake_redis

    assert collector.recv_json('cid', timeout_ms=-1) == [1, 2, 3]
    fake_redis.lpop.assert_called_once()


def test_reply_collector_non_blocking_empty_raises_timeout() -> None:
    fake_redis = MagicMock()
    fake_redis.lpop.return_value = None
    collector = ReplyCollector.__new__(ReplyCollector)
    collector._redis = fake_redis

    with pytest.raises(ReplyTimeoutError):
        collector.recv_json('cid', timeout_ms=0)


def test_reply_collector_singleton_rejects_second_init() -> None:
    sentinel = cast(ReplyCollector, object())
    original = ReplyCollector.INSTANCE
    try:
        ReplyCollector.INSTANCE = sentinel
        with pytest.raises(ValueError, match='instance already exists'):
            ReplyCollector()
    finally:
        ReplyCollector.INSTANCE = original


def test_reply_collector_get_instance_creates_once() -> None:
    original = ReplyCollector.INSTANCE
    ReplyCollector.INSTANCE = None
    try:
        with mock.patch(
            'anthias_common.utils.connect_to_redis', return_value=MagicMock()
        ):
            inst = ReplyCollector.get_instance()
        assert inst is ReplyCollector.INSTANCE
        with mock.patch(
            'anthias_common.utils.connect_to_redis', return_value=MagicMock()
        ):
            assert ReplyCollector.get_instance() is inst
    finally:
        ReplyCollector.INSTANCE = original


# ---------------------------------------------------------------------------
# viewer.messaging.ViewerSubscriber._consume
# ---------------------------------------------------------------------------


def _make_subscriber(commands: dict[str, Any]) -> ViewerSubscriber:
    return ViewerSubscriber(MagicMock(), commands, topic='viewer')


def test_subscriber_dispatches_command_with_parameter() -> None:
    handler = MagicMock()
    sub = _make_subscriber({'next': handler})
    pubsub = MagicMock()
    pubsub.listen.return_value = iter([{'data': 'viewer next&5'}])
    sub._consume(pubsub)
    handler.assert_called_once_with('5')


def test_subscriber_dispatches_command_without_parameter() -> None:
    handler = MagicMock()
    sub = _make_subscriber({'reload': handler})
    pubsub = MagicMock()
    pubsub.listen.return_value = iter([{'data': 'viewer reload'}])
    sub._consume(pubsub)
    # No '&' in payload → parameter is None.
    handler.assert_called_once_with(None)


def test_subscriber_skips_messages_with_wrong_topic() -> None:
    handler = MagicMock()
    unknown_handler = MagicMock()
    sub = _make_subscriber({'next': handler, 'unknown': unknown_handler})
    pubsub = MagicMock()
    pubsub.listen.return_value = iter([{'data': 'somethingelse next'}])
    sub._consume(pubsub)
    handler.assert_not_called()
    unknown_handler.assert_not_called()


def test_subscriber_skips_messages_with_empty_body() -> None:
    handler = MagicMock()
    sub = _make_subscriber({'next': handler})
    pubsub = MagicMock()
    pubsub.listen.return_value = iter([{'data': 'viewer'}])
    sub._consume(pubsub)
    handler.assert_not_called()


def test_subscriber_skips_non_string_data() -> None:
    handler = MagicMock()
    sub = _make_subscriber({'next': handler})
    pubsub = MagicMock()
    pubsub.listen.return_value = iter(
        [{'data': 1}, {'data': b'bytes'}, {'data': None}]
    )
    sub._consume(pubsub)
    handler.assert_not_called()


def test_subscriber_falls_back_to_unknown_handler() -> None:
    unknown = MagicMock()
    sub = _make_subscriber({'unknown': unknown})
    pubsub = MagicMock()
    pubsub.listen.return_value = iter([{'data': 'viewer mystery&x'}])
    sub._consume(pubsub)
    unknown.assert_called_once_with('x')


def test_subscriber_no_handler_when_unknown_missing() -> None:
    sub = _make_subscriber({'next': MagicMock()})
    pubsub = MagicMock()
    pubsub.listen.return_value = iter([{'data': 'viewer mystery'}])
    # Should not raise.
    sub._consume(pubsub)


def test_subscriber_run_signals_ready_then_exits_on_loop_break(
    monkeypatch: Any,
) -> None:
    """run() should call subscribe(), set the readiness flag, and consume.

    We fake _consume to raise once it's called so the outer while loop
    exits on the next iteration via a sentinel-driven side effect.
    """
    redis_conn = MagicMock()
    pubsub = MagicMock()
    redis_conn.pubsub.return_value = pubsub

    sub = ViewerSubscriber(redis_conn, {'next': MagicMock()})

    consume_calls: list[int] = []

    def fake_consume(pubsub_arg: Any) -> None:
        consume_calls.append(1)
        # Second iteration would loop forever; raise a connection
        # error and then patch sleep to short-circuit to a final
        # ConnectionError that breaks out of the while True.
        raise redis.ConnectionError()

    monkeypatch.setattr(sub, '_consume', fake_consume)

    # After the first ConnectionError, the sleep+retry cycle would
    # spin forever. Patch sleep to raise so the test exits cleanly.
    sleep_calls: list[float] = []

    def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        raise SystemExit  # break out of while True

    with (
        mock.patch('anthias_viewer.messaging.sleep', side_effect=fake_sleep),
        pytest.raises(SystemExit),
    ):
        sub.run()

    # First subscribe succeeded → readiness signalled True before
    # disconnect, then False on connection loss.
    pubsub.subscribe.assert_called_once_with(settings_module.VIEWER_CHANNEL)
    set_calls = redis_conn.set.call_args_list
    keys_set = [call.args[0] for call in set_calls]
    assert 'viewer-subscriber-ready' in keys_set
    assert len(consume_calls) == 1
    assert sleep_calls == [ViewerSubscriber.INITIAL_RETRY_DELAY_S]
    pubsub.close.assert_called_once()
