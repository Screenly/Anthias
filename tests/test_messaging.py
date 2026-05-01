import json
import logging
from typing import Any
from unittest import mock
from unittest.mock import MagicMock

import pytest
import redis

import settings as settings_module
from lib.errors import ReplyTimeoutError
from settings import (
    REPLY_KEY_PREFIX,
    ReplyCollector,
    ReplySender,
    ViewerPublisher,
)
from viewer.messaging import ViewerSubscriber

logging.disable(logging.CRITICAL)


# ---------------------------------------------------------------------------
# settings.ViewerPublisher / ReplySender / ReplyCollector
# ---------------------------------------------------------------------------


def test_viewer_publisher_send_publishes_correct_payload() -> None:
    fake_redis = MagicMock()
    publisher = ViewerPublisher.__new__(ViewerPublisher)
    publisher._redis = fake_redis  # type: ignore[attr-defined]

    publisher.send_to_viewer('next')
    fake_redis.publish.assert_called_once_with(
        settings_module.VIEWER_CHANNEL, 'viewer next'
    )


def test_viewer_publisher_singleton_rejects_second_init() -> None:
    sentinel = object()
    original = ViewerPublisher.INSTANCE
    try:
        ViewerPublisher.INSTANCE = sentinel  # type: ignore[assignment]
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
            'lib.utils.connect_to_redis', return_value=MagicMock()
        ):
            inst = ViewerPublisher.get_instance()
        assert inst is ViewerPublisher.INSTANCE
        # Calling get_instance again returns the cached instance.
        with mock.patch(
            'lib.utils.connect_to_redis', return_value=MagicMock()
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


def test_reply_collector_recv_blocking_returns_decoded_payload() -> None:
    fake_redis = MagicMock()
    fake_redis.blpop.return_value = (
        f'{REPLY_KEY_PREFIX}cid'.encode(),
        json.dumps({'ok': True}),
    )
    collector = ReplyCollector.__new__(ReplyCollector)
    collector._redis = fake_redis  # type: ignore[attr-defined]

    result = collector.recv_json('cid', timeout_ms=2000)
    assert result == {'ok': True}
    fake_redis.blpop.assert_called_once_with(
        f'{REPLY_KEY_PREFIX}cid', timeout=2
    )


def test_reply_collector_blocking_rounds_up_to_next_second() -> None:
    fake_redis = MagicMock()
    fake_redis.blpop.return_value = (b'k', json.dumps(1))
    collector = ReplyCollector.__new__(ReplyCollector)
    collector._redis = fake_redis  # type: ignore[attr-defined]

    collector.recv_json('cid', timeout_ms=1500)
    # 1500ms → 2 seconds.
    fake_redis.blpop.assert_called_once_with(
        f'{REPLY_KEY_PREFIX}cid', timeout=2
    )


def test_reply_collector_blocking_timeout() -> None:
    fake_redis = MagicMock()
    fake_redis.blpop.return_value = None
    collector = ReplyCollector.__new__(ReplyCollector)
    collector._redis = fake_redis  # type: ignore[attr-defined]

    with pytest.raises(ReplyTimeoutError):
        collector.recv_json('cid', timeout_ms=100)


def test_reply_collector_non_blocking_uses_lpop() -> None:
    fake_redis = MagicMock()
    fake_redis.lpop.return_value = json.dumps({'value': 42})
    collector = ReplyCollector.__new__(ReplyCollector)
    collector._redis = fake_redis  # type: ignore[attr-defined]

    assert collector.recv_json('cid', timeout_ms=0) == {'value': 42}
    fake_redis.lpop.assert_called_once_with(f'{REPLY_KEY_PREFIX}cid')
    fake_redis.blpop.assert_not_called()


def test_reply_collector_non_blocking_negative_timeout_uses_lpop() -> None:
    fake_redis = MagicMock()
    fake_redis.lpop.return_value = json.dumps([1, 2, 3])
    collector = ReplyCollector.__new__(ReplyCollector)
    collector._redis = fake_redis  # type: ignore[attr-defined]

    assert collector.recv_json('cid', timeout_ms=-1) == [1, 2, 3]
    fake_redis.lpop.assert_called_once()


def test_reply_collector_non_blocking_empty_raises_timeout() -> None:
    fake_redis = MagicMock()
    fake_redis.lpop.return_value = None
    collector = ReplyCollector.__new__(ReplyCollector)
    collector._redis = fake_redis  # type: ignore[attr-defined]

    with pytest.raises(ReplyTimeoutError):
        collector.recv_json('cid', timeout_ms=0)


def test_reply_collector_singleton_rejects_second_init() -> None:
    sentinel = object()
    original = ReplyCollector.INSTANCE
    try:
        ReplyCollector.INSTANCE = sentinel  # type: ignore[assignment]
        with pytest.raises(ValueError, match='instance already exists'):
            ReplyCollector()
    finally:
        ReplyCollector.INSTANCE = original


def test_reply_collector_get_instance_creates_once() -> None:
    original = ReplyCollector.INSTANCE
    ReplyCollector.INSTANCE = None
    try:
        with mock.patch(
            'lib.utils.connect_to_redis', return_value=MagicMock()
        ):
            inst = ReplyCollector.get_instance()
        assert inst is ReplyCollector.INSTANCE
        with mock.patch(
            'lib.utils.connect_to_redis', return_value=MagicMock()
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

    with mock.patch('viewer.messaging.sleep', side_effect=fake_sleep):
        with pytest.raises(SystemExit):
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
