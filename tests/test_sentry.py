"""Regression guard for the Sentry test-mode gate in settings.py.

The unit suite must run with no external network dependencies
(conftest.py force-mocks Redis for the same reason), and exceptions
raised on purpose by failing tests must never reach the production
Sentry project. settings.py defaults the DSN to '' whenever
ENVIRONMENT=test or pytest is detected on argv — this test pins that
behaviour: under pytest the client has no DSN, builds no transport,
and capture calls are dropped.
"""

from pathlib import Path

import sentry_sdk


def test_sentry_does_not_send_under_pytest() -> None:
    client = sentry_sdk.get_client()
    assert not client.dsn
    assert client.transport is None
    # capture_message returns the event id when an event is queued
    # for sending; None means the event was dropped client-side.
    assert sentry_sdk.capture_message('must not be sent') is None


class TestBeforeSendTransientNoise:
    """The before_send hook must drop expected transient states
    (redis restarting, an HTTP client hanging up) and nothing else.
    Regression coverage for Sentry ANTHIAS-M / ANTHIAS-K / ANTHIAS-H /
    ANTHIAS-J (redis blips) and ANTHIAS-N (client disconnect)."""

    @staticmethod
    def _hint_for(exc: BaseException) -> dict:
        try:
            raise exc
        except BaseException as caught:  # noqa: BLE001 — CancelledError
            return {'exc_info': (type(caught), caught, caught.__traceback__)}

    def test_drops_redis_connection_error(self) -> None:
        import redis.exceptions

        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        hint = self._hint_for(
            redis.exceptions.ConnectionError(
                'Error 111 connecting to redis:6379. Connection refused.'
            )
        )
        assert _sentry_before_send({'event_id': 'x'}, hint) is None

    def test_drops_wrapped_redis_connection_error(self) -> None:
        # channels-redis / kombu re-raise the underlying redis error
        # wrapped in their own exception types — the chain has to be
        # walked, not just the head.
        import redis.exceptions

        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        try:
            try:
                raise redis.exceptions.ConnectionError('refused')
            except redis.exceptions.ConnectionError as inner:
                raise RuntimeError('channel layer send failed') from inner
        except RuntimeError as wrapper:
            hint = {
                'exc_info': (
                    type(wrapper),
                    wrapper,
                    wrapper.__traceback__,
                )
            }
        assert _sentry_before_send({'event_id': 'x'}, hint) is None

    def test_drops_cancelled_error(self) -> None:
        import asyncio

        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        hint = self._hint_for(asyncio.CancelledError())
        assert _sentry_before_send({'event_id': 'x'}, hint) is None

    def test_keeps_ordinary_exceptions(self) -> None:
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        event = {'event_id': 'x'}
        hint = self._hint_for(ValueError('a real bug'))
        assert _sentry_before_send(event, hint) is event

    def test_keeps_events_without_exc_info(self) -> None:
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        event = {'event_id': 'x'}
        assert _sentry_before_send(event, {}) is event

    def test_celery_reconnect_logger_is_ignored(self) -> None:
        # celery's consumer retries broker connections on its own but
        # logs each attempt at ERROR; the logger is silenced at init.
        from sentry_sdk.integrations.logging import _IGNORED_LOGGERS

        assert 'celery.worker.consumer.consumer' in _IGNORED_LOGGERS


class TestGetBoardModel:
    """Board-model detection feeding the fleet-triage Sentry tags."""

    def test_reads_and_strips_nul_terminated_model(
        self, tmp_path: Path
    ) -> None:
        from anthias_server.django_project.settings import get_board_model

        model_file = tmp_path / 'model'
        model_file.write_bytes(b'Raspberry Pi 3 Model B Rev 1.2\x00')
        assert (
            get_board_model(str(model_file))
            == 'Raspberry Pi 3 Model B Rev 1.2'
        )

    def test_returns_empty_when_no_device_tree(self, tmp_path: Path) -> None:
        # x86 hosts have no /proc/device-tree at all.
        from anthias_server.django_project.settings import get_board_model

        assert get_board_model(str(tmp_path / 'missing')) == ''
