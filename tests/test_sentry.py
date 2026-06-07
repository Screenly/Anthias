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
from unittest import mock

import pytest
import sentry_sdk
from sentry_sdk.types import Event


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
    def _hint_for(exc: BaseException) -> dict[str, object]:
        # Build the exc_info triple directly instead of raise/except —
        # before_send only inspects exc_info[1] and its
        # __cause__/__context__ chain, and not catching BaseException
        # keeps Sonar S5754 happy.
        return {'exc_info': (type(exc), exc, None)}

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

    def test_drops_redis_timeout_error(self) -> None:
        # redis-py's TimeoutError is a *sibling* of ConnectionError
        # under RedisError, not a subclass — the same transient outage
        # when the socket hangs instead of refusing. A post-deploy
        # event slipped through on this branch (Sentry ANTHIAS-1B).
        import redis.exceptions

        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        assert not issubclass(
            redis.exceptions.TimeoutError,
            redis.exceptions.ConnectionError,
        )
        hint = self._hint_for(
            redis.exceptions.TimeoutError('Timeout connecting to server')
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

    def test_keeps_wrapper_when_context_is_suppressed(self) -> None:
        # ``raise ... from None`` detaches the causal chain — a redis
        # error that merely *preceded* the wrapper must not drop it.
        import redis.exceptions

        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        try:
            try:
                raise redis.exceptions.ConnectionError('refused')
            except redis.exceptions.ConnectionError:
                raise RuntimeError('a real bug') from None
        except RuntimeError as wrapper:
            hint = {
                'exc_info': (
                    type(wrapper),
                    wrapper,
                    wrapper.__traceback__,
                )
            }
        event: Event = {'event_id': 'x'}
        assert _sentry_before_send(event, hint) == event

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

        event: Event = {'event_id': 'x'}
        hint = self._hint_for(ValueError('a real bug'))
        assert _sentry_before_send(event, hint) == event

    def test_keeps_events_without_exc_info(self) -> None:
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        event: Event = {'event_id': 'x'}
        assert _sentry_before_send(event, {}) == event

    def test_celery_reconnect_logger_is_ignored(self) -> None:
        # celery's consumer retries broker connections on its own but
        # logs each attempt at ERROR; the logger is silenced at init.
        # The ignore_logger call happens at settings-module import —
        # import it explicitly so this test passes in isolation too.
        import anthias_server.django_project.settings  # noqa: F401
        from sentry_sdk.integrations.logging import _IGNORED_LOGGERS

        assert 'celery.worker.consumer.consumer' in _IGNORED_LOGGERS
        # The embedded beat scheduler retries broker connections the
        # same way and logs each attempt at ERROR too.
        assert 'celery.beat' in _IGNORED_LOGGERS


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


class TestGetSentryRelease:
    """Release stamping — CalVer + the image's git short hash, so
    pre- and post-deploy builds of the same CalVer are
    distinguishable (the 2026.6.2 audit gap)."""

    def test_appends_short_hash_when_env_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from anthias_server.django_project import settings as s

        monkeypatch.setenv('GIT_SHORT_HASH', 'abc1234')
        with mock.patch.object(
            s, 'get_anthias_release', return_value='2026.6.2'
        ):
            assert s.get_sentry_release() == '2026.6.2+abc1234'

    def test_bare_calver_without_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from anthias_server.django_project import settings as s

        monkeypatch.delenv('GIT_SHORT_HASH', raising=False)
        with mock.patch.object(
            s, 'get_anthias_release', return_value='2026.6.2'
        ):
            assert s.get_sentry_release() == '2026.6.2'

    def test_none_when_version_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No bogus '+hash'-only release when the CalVer itself is
        # missing — Sentry should see no release at all.
        from anthias_server.django_project import settings as s

        monkeypatch.setenv('GIT_SHORT_HASH', 'abc1234')
        with mock.patch.object(s, 'get_anthias_release', return_value=''):
            assert s.get_sentry_release() is None


class TestIsBalenaDeploy:
    """The balena tag's decision logic — must match what
    anthias_common.utils.is_balena_app derives from the BALENA env
    var the balena supervisor injects."""

    def test_true_under_balena(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from anthias_server.django_project import settings as s

        monkeypatch.setenv('BALENA', '1')
        assert s.is_balena_deploy() is True

    def test_false_on_compose_installs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from anthias_server.django_project import settings as s

        monkeypatch.delenv('BALENA', raising=False)
        assert s.is_balena_deploy() is False

    def test_agrees_with_the_canonical_helper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The settings copy is inlined for import-weight reasons —
        # pin it against the canonical helper so they can't drift.
        from anthias_common.utils import is_balena_app
        from anthias_server.django_project import settings as s

        for value in (None, '1'):
            if value is None:
                monkeypatch.delenv('BALENA', raising=False)
            else:
                monkeypatch.setenv('BALENA', value)
            assert s.is_balena_deploy() == is_balena_app()
