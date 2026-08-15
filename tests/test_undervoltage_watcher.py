"""Tests for the background under-voltage watcher.

The watcher holds the subtlest part of the feature: the sysfs
``POLLPRI`` protocol, the interaction between event-driven wakeups and
the periodic re-sample, and recovery when the sensor disappears. It is
driven here through a fake ``select.poll`` so the loop can be stepped
deterministically without a real Pi or real timing.
"""

import select
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock
from unittest.mock import MagicMock

import pytest

from anthias_common import undervoltage
from anthias_server.lib import undervoltage_watcher


class _StopLoop(BaseException):
    """Breaks out of the watcher's infinite loop inside a test.

    Inherits ``BaseException``, not ``Exception``, on purpose: the
    watcher's recovery handler catches ``Exception`` by design, so an
    ``Exception`` subclass here would be swallowed into the 60-second
    backoff and the test would hang forever instead of stopping.
    """


class FakePoll:
    """Stands in for ``select.poll()``.

    Yields one scripted result per ``poll()`` call, then raises
    ``_StopLoop`` so the watcher's ``while True`` terminates. Each
    scripted entry is the event list ``poll()`` returns: ``[]`` for a
    timeout, ``[(fd, select.POLLPRI)]`` for a change notification.
    """

    def __init__(self, results: list[list[tuple[int, int]]]) -> None:
        self._results = list(results)
        self.registered: list[Any] = []

    def register(self, fd: Any, mask: int) -> None:
        self.registered.append((fd, mask))

    def poll(self, timeout_ms: float | None = None) -> list[tuple[int, int]]:
        if not self._results:
            raise _StopLoop
        return self._results.pop(0)


@pytest.fixture(autouse=True)
def _clear_alarm_path_cache() -> Iterator[None]:
    undervoltage.reset_alarm_path_cache()
    yield
    undervoltage.reset_alarm_path_cache()


@pytest.fixture
def fake_redis() -> MagicMock:
    from conftest import _make_fake_redis

    return _make_fake_redis()


@pytest.fixture
def alarm_file(tmp_path: Path) -> Path:
    path = tmp_path / 'in0_lcrit_alarm'
    path.write_text('0\n')
    return path


WATCHER = 'anthias_server.lib.undervoltage_watcher'


@contextmanager
def _sensor_removed(
    poller: FakePoll,
) -> Iterator[tuple[MagicMock, MagicMock]]:
    """Patch the watcher so any failure ends in "sensor gone, stop".

    Bundles the four seams the recovery path touches: the scripted
    poller, a no-op backoff sleep, an attribute path that no longer
    exists, and a rescan that finds nothing. Yields the sleep and
    rescan mocks so a test can assert the loop backed off exactly
    once rather than spinning.
    """
    with (
        mock.patch(f'{WATCHER}.select.poll', return_value=poller),
        mock.patch(f'{WATCHER}.time.sleep') as sleep_mock,
        mock.patch(f'{WATCHER}.os.path.exists', return_value=False),
        mock.patch(
            f'{WATCHER}.undervoltage.find_alarm_path', return_value=None
        ) as find_mock,
    ):
        yield sleep_mock, find_mock


def _latch(redis_client: Any) -> dict[str, Any]:
    """The persisted latch.

    Assertions read this rather than ``get_state()``: that helper
    re-reads the live sysfs attribute, and the test host has no real
    rpi_volt sensor, so it would report ``supported: False`` and mask
    what the watcher actually wrote.
    """
    import json

    raw = redis_client.get(undervoltage.REDIS_KEY)
    assert raw, 'watcher wrote nothing to the latch'
    return dict(json.loads(raw))


def _run_loop(
    alarm_path: Path,
    redis_client: Any,
    results: list[list[tuple[int, int]]],
) -> FakePoll:
    """Drive ``_watch_loop`` through a scripted sequence of poll results."""
    poller = FakePoll(results)
    with (
        mock.patch(
            'anthias_server.lib.undervoltage_watcher.select.poll',
            return_value=poller,
        ),
        pytest.raises(_StopLoop),
    ):
        undervoltage_watcher._watch_loop(str(alarm_path), redis_client)
    return poller


class TestWatchLoop:
    def test_records_a_brown_out_on_a_kernel_event(
        self, alarm_file: Path, fake_redis: MagicMock
    ) -> None:
        alarm_file.write_text('1\n')

        _run_loop(alarm_file, fake_redis, [[(3, 2)]])

        state = _latch(fake_redis)
        assert state['active'] is True
        assert state['count'] == 1

    def test_resampling_a_sustained_alarm_counts_once(
        self, alarm_file: Path, fake_redis: MagicMock
    ) -> None:
        # This is the regression the counter was getting wrong: the
        # loop wakes on its 30s timeout as well as on kernel events,
        # and an unchanged alarm across ten wakeups is one brown-out,
        # not ten. The banner renders this number to the operator.
        alarm_file.write_text('1\n')

        _run_loop(alarm_file, fake_redis, [[] for _ in range(10)])

        assert _latch(fake_redis)['count'] == 1

    def test_separate_dips_count_separately(
        self, alarm_file: Path, fake_redis: MagicMock
    ) -> None:
        # Flip the file between wakeups: 1 -> 0 -> 1 is two events.
        calls = {'n': 0}
        sequence = ['1\n', '0\n', '1\n']

        def flip(*_a: Any, **_k: Any) -> list[tuple[int, int]]:
            if calls['n'] >= len(sequence):
                raise _StopLoop
            alarm_file.write_text(sequence[calls['n']])
            calls['n'] += 1
            return []

        poller = FakePoll([])
        poller.poll = flip  # type: ignore[method-assign]
        with (
            mock.patch(
                'anthias_server.lib.undervoltage_watcher.select.poll',
                return_value=poller,
            ),
            pytest.raises(_StopLoop),
        ):
            undervoltage_watcher._watch_loop(str(alarm_file), fake_redis)

        assert _latch(fake_redis)['count'] == 2

    def test_recovery_clears_active_but_keeps_the_history(
        self, alarm_file: Path, fake_redis: MagicMock
    ) -> None:
        alarm_file.write_text('1\n')
        _run_loop(alarm_file, fake_redis, [[]])
        alarm_file.write_text('0\n')
        _run_loop(alarm_file, fake_redis, [[]])

        state = _latch(fake_redis)
        assert state['active'] is False
        assert state['seen_since_boot'] is True

    def test_registers_for_pollpri(
        self, alarm_file: Path, fake_redis: MagicMock
    ) -> None:
        # The whole design rests on sysfs change notification; a
        # registration without POLLPRI would silently degrade the
        # watcher to a 30-second sampler.
        poller = _run_loop(alarm_file, fake_redis, [[]])

        assert poller.registered
        _fd, mask = poller.registered[0]
        assert mask & select.POLLPRI


class TestWatchLoopErrorHandling:
    def test_pollerr_does_not_spin(
        self, alarm_file: Path, fake_redis: MagicMock
    ) -> None:
        # A removed hwmon device leaves poll() returning instantly
        # forever. The loop must treat that as an error and fall
        # through to the backoff/reopen path rather than burning CPU.
        with _sensor_removed(FakePoll([[(3, select.POLLERR)]])) as (
            sleep_mock,
            _find_mock,
        ):
            undervoltage_watcher._watch_loop(str(alarm_file), fake_redis)

        # Backed off once, then stopped because the sensor was gone.
        assert sleep_mock.call_count == 1

    def test_empty_read_is_not_treated_as_healthy(
        self, alarm_file: Path, fake_redis: MagicMock
    ) -> None:
        # Seed a live alarm, then make the attribute read empty. An
        # empty read must not be mapped to "0" and clear the warning.
        undervoltage.record_observation(fake_redis, True)
        alarm_file.write_text('')

        with _sensor_removed(FakePoll([[]])):
            undervoltage_watcher._watch_loop(str(alarm_file), fake_redis)

        assert _latch(fake_redis)['seen_since_boot'] is True

    def test_stops_when_the_sensor_is_gone_for_good(
        self, tmp_path: Path, fake_redis: MagicMock
    ) -> None:
        missing = tmp_path / 'gone'

        with _sensor_removed(FakePoll([[]])) as (_sleep_mock, find_mock):
            undervoltage_watcher._watch_loop(str(missing), fake_redis)

        # Bypasses the cache, since a rebind is exactly when it is stale.
        find_mock.assert_called_once_with(use_cache=False)


class TestStart:
    def test_no_op_without_a_sensor(self, fake_redis: MagicMock) -> None:
        with mock.patch(
            'anthias_server.lib.undervoltage_watcher.undervoltage.find_alarm_path',
            return_value=None,
        ):
            assert undervoltage_watcher.start(fake_redis) is False

    def test_seeds_the_latch_and_starts_one_thread(
        self, alarm_file: Path, fake_redis: MagicMock
    ) -> None:
        alarm_file.write_text('1\n')
        undervoltage_watcher._thread = None

        with mock.patch(
            'anthias_server.lib.undervoltage_watcher.threading.Thread'
        ) as thread_cls:
            thread_cls.return_value.is_alive.return_value = True
            with mock.patch(
                'anthias_server.lib.undervoltage_watcher.undervoltage.find_alarm_path',
                return_value=str(alarm_file),
            ):
                assert undervoltage_watcher.start(fake_redis) is True
                # Idempotent: a second call must not stack threads.
                assert undervoltage_watcher.start(fake_redis) is True

        assert thread_cls.call_count == 1
        assert _latch(fake_redis)['count'] == 1
        undervoltage_watcher._thread = None
