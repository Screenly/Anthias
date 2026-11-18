"""Regression guard for the Sentry test-mode gate in settings.py.

The unit suite must run with no external network dependencies
(conftest.py force-mocks Redis for the same reason), and exceptions
raised on purpose by failing tests must never reach the production
Sentry project. settings.py defaults the DSN to '' whenever
ENVIRONMENT=test or pytest is detected on argv — this test pins that
behaviour: under pytest the client has no DSN, builds no transport,
and capture calls are dropped.
"""

import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest
import sentry_sdk
from celery.exceptions import SoftTimeLimitExceeded
from sentry_sdk.types import Event

from anthias_server.lib import cec_client


def _soft_limit_from_third_party_teardown() -> SoftTimeLimitExceeded:
    """A soft limit whose traceback holds no Anthias frame.

    Stands in for celery's ``on_task_postrun`` fixup and billiard's idle
    workloop: this test module is not an ``anthias_*`` package, so the
    only frame on the traceback is a non-Anthias one.
    """
    try:
        raise SoftTimeLimitExceeded()
    except SoftTimeLimitExceeded as exc:
        return exc


def _soft_limit_raised_in_anthias_code() -> SoftTimeLimitExceeded:
    """A soft limit that really did interrupt Anthias code.

    Driven through ``cec_client.available`` — the real call site that
    used to swallow it — so the traceback carries an ``anthias_server``
    frame the way a live one would.
    """
    with mock.patch.object(
        cec_client, 'connect_to_redis', side_effect=SoftTimeLimitExceeded()
    ):
        try:
            cec_client.available()
        except SoftTimeLimitExceeded as exc:
            return exc
    raise AssertionError('available() swallowed the soft time limit')


def _anthias_error_wrapping_a_late_soft_limit() -> BaseException:
    """An Anthias error raised *from* a soft limit that landed in
    third-party frames.

    The wrapper is a real bug and must report. Dropping it because its
    cause looks like teardown noise is the one failure mode a
    before_send filter must never have — the report just vanishes.
    """
    try:
        raise SoftTimeLimitExceeded()
    except SoftTimeLimitExceeded as cause:
        try:
            raise RuntimeError('a real failure in a task') from cause
        except RuntimeError as wrapper:
            return wrapper


def test_sentry_does_not_send_under_pytest() -> None:
    client = sentry_sdk.get_client()
    assert not client.dsn
    assert client.transport is None
    # capture_message returns the event id when an event is queued
    # for sending; None means the event was dropped client-side.
    assert sentry_sdk.capture_message('must not be sent') is None


@pytest.fixture(autouse=True)
def _reset_sentry_throttle() -> Iterator[None]:
    """Clear the before_send repetition throttle around every test.

    Its counters live at module scope in settings.py, so without this
    a key counted by one test is still counted in the next, and an
    "this event is kept" assertion starts failing on whichever test
    happens to raise that exception type for the third time.
    """
    from anthias_server.django_project.settings import (
        _sentry_throttle_state,
    )

    _sentry_throttle_state.clear()
    yield
    _sentry_throttle_state.clear()


class TestBeforeSendRepetitionThrottle:
    """One looping device must not spend the whole Sentry quota.

    What exhausts the quota is depth rather than breadth: a single
    board accounted for 1,761 of ANTHIAS-3H's 1,762 events, and
    another for 1,063 of ANTHIAS-2D's 1,093, crowding out every other
    issue for the rest of the billing period. before_send keeps the
    powers of two within an hour-long window per issue and drops what
    falls between them, so a loop costs O(log n) events.
    """

    @staticmethod
    def _hint_for(exc: BaseException) -> dict[str, object]:
        return {'exc_info': (type(exc), exc, None)}

    @staticmethod
    def _log_hint(template: str, *args: object) -> dict[str, object]:
        return {
            'log_record': logging.LogRecord(
                name='celery.worker',
                level=logging.ERROR,
                pathname='worker.py',
                lineno=1,
                msg=template,
                args=args,
                exc_info=None,
            )
        }

    def test_keeps_the_powers_of_two_and_drops_the_rest(self) -> None:
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        kept = [
            occurrence
            for occurrence in range(1, 33)
            if _sentry_before_send(
                {'event_id': str(occurrence)},
                self._hint_for(ValueError('the same loop')),
            )
            is not None
        ]
        assert kept == [1, 2, 4, 8, 16, 32]

    def test_first_event_of_a_window_is_untagged(self) -> None:
        # Nothing has been thinned yet, so an occurrence tag would be
        # noise on the overwhelmingly common single-event case.
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        event: Event = {'event_id': 'x'}
        assert _sentry_before_send(event, self._hint_for(ValueError())) is (
            event
        )
        assert 'tags' not in event

    def test_survivors_carry_the_true_occurrence_count(self) -> None:
        # The thinning is lossy about *count*, so the count has to
        # ride along: triage must be able to tell the 4th occurrence
        # from the 4th distinct event.
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        tagged: list[str | None] = []
        for occurrence in range(1, 9):
            event: Event = {'event_id': str(occurrence)}
            if (
                _sentry_before_send(event, self._hint_for(ValueError()))
                is not None
            ):
                tagged.append(event.get('tags', {}).get('throttle.occurrence'))
        assert tagged == [None, '2', '4', '8']

    def test_preserves_an_existing_tag_mapping(self) -> None:
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        # Occurrences 1, 2 and 4 are kept and 3 is dropped, so the
        # last event here is the 4th — a tagged survivor.
        for _ in range(4):
            event: Event = {'event_id': 'x', 'tags': {'device_type': 'pi5'}}
            _sentry_before_send(event, self._hint_for(ValueError()))
        assert event['tags'] == {
            'device_type': 'pi5',
            'throttle.occurrence': '4',
        }

    def test_distinct_issues_do_not_share_a_budget(self) -> None:
        # The quota problem is depth, not breadth. A board looping on
        # one error must never mask a different error beside it.
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        for _ in range(50):
            _sentry_before_send(
                {'event_id': 'x'}, self._hint_for(ValueError())
            )
        event: Event = {'event_id': 'other'}
        assert _sentry_before_send(event, self._hint_for(TypeError())) is (
            event
        )

    def test_noise_dropped_earlier_does_not_spend_a_budget(self) -> None:
        # The throttle runs last on purpose: a redis outage filtered
        # out above is not a report, and must not throttle the real
        # error raised alongside it.
        import redis.exceptions

        from anthias_server.django_project.settings import (
            _sentry_before_send,
            _sentry_throttle_state,
        )

        for _ in range(100):
            _sentry_before_send(
                {'event_id': 'x'},
                self._hint_for(redis.exceptions.ConnectionError()),
            )
        assert _sentry_throttle_state == {}

    def test_one_log_template_is_one_key_across_its_arguments(self) -> None:
        # "Timed out waiting for UP message from <ForkPoolWorker-26>"
        # and the same line for worker 27 are one Sentry issue, so
        # they have to share one budget — keying on the formatted
        # message would give every worker its own and throttle
        # nothing (Sentry ANTHIAS-1Q).
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        kept = [
            worker
            for worker in range(1, 17)
            if _sentry_before_send(
                {'event_id': str(worker)},
                self._log_hint(
                    'Timed out waiting for UP message from %s',
                    f'ForkPoolWorker-{worker}',
                ),
            )
            is not None
        ]
        assert kept == [1, 2, 4, 8, 16]

    def test_different_log_templates_are_different_keys(self) -> None:
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        for _ in range(20):
            _sentry_before_send(
                {'event_id': 'x'}, self._log_hint('first template %s', 'a')
            )
        event: Event = {'event_id': 'y'}
        assert _sentry_before_send(
            event, self._log_hint('second template %s', 'a')
        ) == (event)

    def test_an_event_it_cannot_key_is_never_throttled(self) -> None:
        # A key coarse enough to merge unrelated issues could drop a
        # report with no duplicate anywhere, so an unkeyable event is
        # left alone rather than lumped together.
        from anthias_server.django_project.settings import (
            _sentry_before_send,
            _sentry_throttle_state,
        )

        for _ in range(20):
            event: Event = {'event_id': 'x'}
            assert _sentry_before_send(event, {}) == event
        assert _sentry_throttle_state == {}

    def test_a_stale_window_starts_the_count_again(self) -> None:
        from anthias_server.django_project.settings import (
            _SENTRY_THROTTLE_WINDOW_SECONDS,
            _sentry_before_send,
            _sentry_throttle_state,
            _throttle_key,
        )

        hint = self._hint_for(ValueError())
        key = _throttle_key({'event_id': 'x'}, hint)
        assert key is not None
        # Rewind the window rather than patch the clock: the same
        # state the throttle would be in an hour later, with no
        # global time.monotonic monkeypatch for the rest of the suite.
        _sentry_throttle_state[key] = (
            time.monotonic() - _SENTRY_THROTTLE_WINDOW_SECONDS - 1,
            9999,
        )
        event: Event = {'event_id': 'x'}
        assert _sentry_before_send(event, hint) == event
        assert 'tags' not in event
        assert _sentry_throttle_state[key][1] == 1

    def test_expired_windows_are_evicted_before_live_ones(self) -> None:
        from anthias_server.django_project.settings import (
            _SENTRY_THROTTLE_MAX_KEYS,
            _SENTRY_THROTTLE_WINDOW_SECONDS,
            _sentry_before_send,
            _sentry_throttle_state,
        )

        stale = time.monotonic() - _SENTRY_THROTTLE_WINDOW_SECONDS - 1
        for index in range(_SENTRY_THROTTLE_MAX_KEYS + 100):
            _sentry_throttle_state[('exc', 'm', f'E{index}', '')] = (stale, 1)
        _sentry_before_send({'event_id': 'x'}, self._hint_for(ValueError()))
        assert len(_sentry_throttle_state) == 1

    def test_live_windows_are_capped_by_dropping_the_oldest(self) -> None:
        from anthias_server.django_project.settings import (
            _SENTRY_THROTTLE_MAX_KEYS,
            _sentry_before_send,
            _sentry_throttle_state,
        )

        now = time.monotonic()
        for index in range(_SENTRY_THROTTLE_MAX_KEYS + 100):
            _sentry_throttle_state[('exc', 'm', f'E{index}', '')] = (
                now + index,
                1,
            )
        _sentry_before_send({'event_id': 'x'}, self._hint_for(ValueError()))
        assert len(_sentry_throttle_state) == _SENTRY_THROTTLE_MAX_KEYS
        # The newest live windows are the ones still counting.
        assert ('exc', 'm', 'E0', '') not in _sentry_throttle_state
        assert (
            'exc',
            'm',
            f'E{_SENTRY_THROTTLE_MAX_KEYS + 99}',
            '',
        ) in _sentry_throttle_state


class TestThrottleKey:
    """What the throttle treats as "the same issue"."""

    def test_prefers_the_innermost_anthias_frame(self) -> None:
        from anthias_server.django_project.settings import _throttle_key
        from anthias_server.lib import cec_client

        with mock.patch.object(
            cec_client, 'connect_to_redis', side_effect=ValueError('boom')
        ):
            try:
                cec_client.available()
            except ValueError as exc:
                key = _throttle_key(
                    {'event_id': 'x'},
                    {'exc_info': (type(exc), exc, exc.__traceback__)},
                )
        assert key is not None
        assert key[0] == 'exc'
        assert key[3].startswith('anthias_server.lib.cec_client:')

    def test_falls_back_to_the_innermost_frame_without_our_code(
        self,
    ) -> None:
        # No Anthias frame anywhere: the raise site is still the most
        # specific thing available, so the key uses it rather than
        # giving up and leaving a third-party loop unthrottled.
        from anthias_server.django_project.settings import _throttle_key

        try:
            json.loads('{nope')
        except ValueError as exc:
            key = _throttle_key(
                {'event_id': 'x'},
                {'exc_info': (type(exc), exc, exc.__traceback__)},
            )
        assert key is not None
        assert key[3].startswith('json')

    def test_has_no_key_for_an_event_with_neither_shape(self) -> None:
        from anthias_server.django_project.settings import _throttle_key

        assert _throttle_key({'event_id': 'x'}, {}) is None


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

    def test_drops_auth_settings_error(self) -> None:
        # AuthSettingsError is operator-facing input validation from
        # the settings-save flow (mismatched password, taken username,
        # weak password), not a bug. The save views log it at warning
        # so it never reaches the logging integration; this is the
        # backstop for any other path (Sentry ANTHIAS-3D).
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )
        from anthias_server.lib.auth import AuthSettingsError

        hint = self._hint_for(AuthSettingsError('New passwords do not match!'))
        assert _sentry_before_send({'event_id': 'x'}, hint) is None

    def test_drops_gaierror(self) -> None:
        # DNS failing = the device is offline, a routine state for
        # signage. A DNS-less box burned an event per attempted
        # external call, mostly via asyncio's "Future exception was
        # never retrieved" log (Sentry ANTHIAS-3G).
        import socket

        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        hint = self._hint_for(
            socket.gaierror(-5, 'No address associated with hostname')
        )
        assert _sentry_before_send({'event_id': 'x'}, hint) is None

    def test_drops_wrapped_gaierror(self) -> None:
        # requests buries the gaierror several layers deep
        # (urllib3 NameResolutionError -> requests.ConnectionError);
        # the chain walk must still find it.
        import socket

        import requests

        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        try:
            try:
                raise socket.gaierror(
                    -5, 'No address associated with hostname'
                )
            except socket.gaierror as root:
                raise requests.exceptions.ConnectionError(
                    'resolution failed'
                ) from root
        except requests.exceptions.ConnectionError as wrapper:
            hint = {
                'exc_info': (
                    type(wrapper),
                    wrapper,
                    wrapper.__traceback__,
                )
            }
        assert _sentry_before_send({'event_id': 'x'}, hint) is None

    def test_drops_yt_dlp_download_error(self) -> None:
        # A yt-dlp DownloadError (network timeout, geo-block, private/
        # deleted video, bad URL) is operator-facing, not a bug — the
        # download task's on_failure records error_message so the asset
        # shows a "Failed" pill (Sentry ANTHIAS-3T). Synthesise the
        # class so the test doesn't depend on importing yt_dlp.
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        download_error_cls = type(
            'DownloadError', (Exception,), {'__module__': 'yt_dlp.utils'}
        )
        hint = self._hint_for(download_error_cls('read timed out'))
        assert _sentry_before_send({'event_id': 'x'}, hint) is None

    def test_keeps_non_ytdlp_download_error(self) -> None:
        # The drop is scoped to yt_dlp by module — a same-named
        # DownloadError from anywhere else must NOT be swallowed.
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        other_cls = type(
            'DownloadError', (Exception,), {'__module__': 'some.other.pkg'}
        )
        event: Event = {'event_id': 'x'}
        hint = self._hint_for(other_cls('a real bug'))
        assert _sentry_before_send(event, hint) == event

    def test_keeps_lookalike_module_download_error(self) -> None:
        # The module match is exact/prefix-scoped so a look-alike
        # package (yt_dlp2, yt_dlp_fake) can't smuggle its own
        # DownloadError through the drop.
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        lookalike_cls = type(
            'DownloadError', (Exception,), {'__module__': 'yt_dlp2.utils'}
        )
        event: Event = {'event_id': 'x'}
        hint = self._hint_for(lookalike_cls('not really yt-dlp'))
        assert _sentry_before_send(event, hint) == event

    def test_drops_short_content_length_runtime_error(self) -> None:
        # A client that hangs up while Django's ASGI handler is still
        # streaming a static file raises a bare RuntimeError with this
        # message — a broken pipe, not a truncated file (Sentry
        # ANTHIAS-37).
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        hint = self._hint_for(
            RuntimeError('Response content shorter than Content-Length')
        )
        assert _sentry_before_send({'event_id': 'x'}, hint) is None

    def test_keeps_unrelated_runtime_error(self) -> None:
        # The drop is scoped to the client-disconnect message — any
        # other RuntimeError is a real bug and must NOT be swallowed.
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        event: Event = {'event_id': 'x'}
        hint = self._hint_for(RuntimeError('something actually broke'))
        assert _sentry_before_send(event, hint) == event

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

    def test_drops_soft_time_limit_raised_outside_our_code(self) -> None:
        # Celery arms the soft-limit timer for the whole job, so a task
        # that finishes a hair under budget can still take the signal
        # during teardown — inside celery's on_task_postrun fixup or
        # billiard's idle workloop. No task handler can catch those and
        # nothing was interrupted (Sentry ANTHIAS-45 / ANTHIAS-5Y).
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        exc = _soft_limit_from_third_party_teardown()
        assert _sentry_before_send({'event_id': 'x'}, self._hint_for(exc)) is (
            None
        )

    def test_keeps_soft_time_limit_that_interrupted_a_task(self) -> None:
        # A soft limit that really did cut Anthias code short has our
        # frames in the traceback and stays reportable.
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        event: Event = {'event_id': 'x'}
        exc = _soft_limit_raised_in_anthias_code()
        assert _sentry_before_send(event, self._hint_for(exc)) == event

    def test_keeps_an_anthias_error_wrapping_a_late_soft_limit(
        self,
    ) -> None:
        # The soft-limit rule is applied to the head exception only,
        # unlike every other rule here. Walking the chain would discard
        # a genuine Anthias failure on the strength of its cause.
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        event: Event = {'event_id': 'x'}
        exc = _anthias_error_wrapping_a_late_soft_limit()
        assert _sentry_before_send(event, self._hint_for(exc)) == event

    def test_keeps_soft_time_limit_without_a_traceback(self) -> None:
        # A missing traceback is "unknown", not "safe to drop".
        from anthias_server.django_project.settings import (
            _sentry_before_send,
        )

        event: Event = {'event_id': 'x'}
        hint = self._hint_for(SoftTimeLimitExceeded())
        assert _sentry_before_send(event, hint) == event

    def test_celery_reconnect_logger_is_ignored(self) -> None:
        # celery's consumer retries broker connections on its own but
        # logs each attempt at ERROR; the logger is silenced at init.
        # The ignore_logger call happens at settings-module import —
        # import it explicitly so this test passes in isolation too.
        from sentry_sdk.integrations.logging import _IGNORED_LOGGERS

        import anthias_server.django_project.settings  # noqa: F401

        assert 'celery.worker.consumer.consumer' in _IGNORED_LOGGERS
        # The embedded beat scheduler retries broker connections the
        # same way and logs each attempt at ERROR too.
        assert 'celery.beat' in _IGNORED_LOGGERS
        # The redis result backend logs its own reconnect retries
        # ("Connection to Redis lost: Retry (4/20)") at ERROR
        # (Sentry ANTHIAS-2E).
        assert 'celery.backends.redis' in _IGNORED_LOGGERS
        # The async result backend logs "Retry limit exceeded while
        # trying to reconnect to the Celery result store backend" at a
        # fatal level on a sustained redis outage (Sentry ANTHIAS-3X).
        assert 'celery.backends.asynchronous' in _IGNORED_LOGGERS

    def test_disallowed_host_logger_is_ignored(self) -> None:
        # An internet-exposed device gets scanned with bogus/spoofed
        # Host headers; Django rejects each with a 400 and logs it at
        # ERROR to django.security.DisallowedHost, which the logging
        # integration would otherwise turn into a Sentry event. It is
        # non-actionable background-scanner noise (Sentry ANTHIAS-2A).
        from sentry_sdk.integrations.logging import _IGNORED_LOGGERS

        import anthias_server.django_project.settings  # noqa: F401

        assert 'django.security.DisallowedHost' in _IGNORED_LOGGERS


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


class TestDefaultDsnByEnvironment:
    """Which environments fall back to the production project.

    An event only reaches the project by consuming a slot in the
    organisation's error quota, so a stack that nobody triages must not
    default into it — the `environment` tag sorts events after they
    have already been counted.
    """

    @staticmethod
    def _default_dsn_for(environment: str, under_pytest: bool) -> str:
        """Re-resolve settings.py's DSN default for one environment.

        settings.py computes this once at import, and the suite runs
        under pytest, so the live value cannot answer the question for
        any other environment.
        """
        is_test = environment == 'test' or under_pytest
        return (
            ''
            if is_test or environment == 'development'
            else (
                'https://da18c7bdab65c9adc4afcd311f5b6f09'
                '@o4511522371534848.ingest.us.sentry.io/4511522375794688'
            )
        )

    @pytest.mark.parametrize('environment', ['test', 'development'])
    def test_unattended_environments_do_not_default_to_production(
        self, environment: str
    ) -> None:
        assert self._default_dsn_for(environment, under_pytest=False) == ''

    def test_production_still_reports(self) -> None:
        assert self._default_dsn_for('production', under_pytest=False)

    def test_pytest_overrides_any_environment(self) -> None:
        assert self._default_dsn_for('production', under_pytest=True) == ''

    def test_settings_module_agrees(self) -> None:
        """Pin the helper above to the real expression.

        The helper mirrors settings.py rather than importing it, so it
        would keep passing if the real branch were reverted.
        """
        source = (
            Path(__file__).resolve().parents[1]
            / 'src/anthias_server/django_project/settings.py'
        ).read_text()

        assert "if IS_TEST or ENVIRONMENT == 'development'" in source
