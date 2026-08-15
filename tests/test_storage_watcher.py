"""Tests for the storage-health watcher thread."""

from __future__ import annotations

import threading
from typing import Any
from unittest import mock

import pytest

from anthias_common import storage_health
from anthias_server.lib import storage_watcher

_PROBE_OK = {
    'supported': True,
    'fstype': 'ext4',
    'device': 'mmcblk0p2',
}


class _Stop(BaseException):
    """Breaks out of the watcher's ``while True``.

    Deliberately a ``BaseException``: the loop catches ``Exception``
    and backs off, so anything narrower would be swallowed and the
    test would hang instead of ending.
    """


@pytest.fixture(autouse=True)
def _reset_watcher() -> Any:
    """The module holds the thread in a global, so leave it as found."""
    storage_watcher._thread = None
    yield
    storage_watcher._thread = None


def _state(**overrides: Any) -> dict[str, Any]:
    state = {
        'status': storage_health.STATUS_OK,
        'device': 'mmcblk0p2',
        'read_only': False,
        'write_reason': None,
        'errors_count': 0,
        'last_error': None,
        'media': {'wear_pct': None, 'pre_eol': None},
    }
    state.update(overrides)
    return state


class TestStart:
    def test_no_op_when_the_filesystem_cannot_be_resolved(self) -> None:
        with mock.patch.object(
            storage_health, 'probe', return_value={'supported': False}
        ):
            assert storage_watcher.start(mock.MagicMock(), '/data') is False

        assert storage_watcher._thread is None

    def test_starts_a_daemon_thread(self) -> None:
        with (
            mock.patch.object(storage_health, 'probe', return_value=_PROBE_OK),
            mock.patch.object(storage_watcher, '_watch_loop'),
        ):
            assert storage_watcher.start(mock.MagicMock(), '/data') is True

        assert storage_watcher._thread is not None
        # Daemon so it can never hold up worker shutdown.
        assert storage_watcher._thread.daemon is True

    def test_does_not_stack_a_second_live_thread(self) -> None:
        # A worker restart in-process must not stack watchers, each one
        # writing its own canary to the card. The real loop never
        # returns, so the stand-in has to block too -- a loop that
        # exited immediately would leave a dead thread, and restarting
        # from that is correct rather than a leak.
        release = threading.Event()

        with (
            mock.patch.object(storage_health, 'probe', return_value=_PROBE_OK),
            mock.patch.object(
                storage_watcher,
                '_watch_loop',
                lambda *_a: release.wait(timeout=5),
            ),
        ):
            try:
                storage_watcher.start(mock.MagicMock(), '/data')
                first = storage_watcher._thread
                storage_watcher.start(mock.MagicMock(), '/data')

                assert storage_watcher._thread is first
            finally:
                release.set()


class TestWatchLoop:
    def test_writes_on_the_first_pass_then_only_samples(self) -> None:
        # The sysfs counters are free and the write check is not, so
        # the loop pays for a write once per WRITE_CHECK_INTERVAL_S and
        # samples in between. The first pass must include a write so
        # the UI is right from startup rather than 15 minutes later.
        sleeps: list[float] = []

        def _sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) >= 2:
                raise _Stop

        with (
            mock.patch.object(
                storage_health, 'record_check', return_value=_state()
            ) as record,
            mock.patch(
                'anthias_server.lib.storage_watcher.time.sleep', _sleep
            ),
            pytest.raises(_Stop),
        ):
            storage_watcher._watch_loop(mock.MagicMock(), '/data')

        assert [c.kwargs['write_check'] for c in record.call_args_list] == [
            True,
            False,
        ]
        assert sleeps == [
            storage_watcher.SAMPLE_INTERVAL_S,
            storage_watcher.SAMPLE_INTERVAL_S,
        ]

    def test_logs_once_per_status_change(self, caplog: Any) -> None:
        # One line per transition, not one per sample: at a 60s cadence
        # the latter would put 1,440 identical warnings a day into the
        # device log an engineer has to read during a support call.
        results = [
            _state(),
            _state(status=storage_health.STATUS_FAILING, read_only=True),
            _state(status=storage_health.STATUS_FAILING, read_only=True),
        ]

        def _record(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return results.pop(0)

        def _sleep(_seconds: float) -> None:
            if not results:
                raise _Stop

        with (
            caplog.at_level('WARNING'),
            mock.patch.object(
                storage_health, 'record_check', side_effect=_record
            ),
            mock.patch(
                'anthias_server.lib.storage_watcher.time.sleep', _sleep
            ),
            pytest.raises(_Stop),
        ):
            storage_watcher._watch_loop(mock.MagicMock(), '/data')

        failing = [
            r for r in caplog.records if 'no longer reliable' in r.message
        ]
        assert len(failing) == 1

    def test_a_failed_pass_backs_off_rather_than_spinning(self) -> None:
        sleeps: list[float] = []

        def _sleep(seconds: float) -> None:
            sleeps.append(seconds)
            raise _Stop

        with (
            mock.patch.object(
                storage_health,
                'record_check',
                side_effect=OSError('sysfs gone'),
            ),
            mock.patch(
                'anthias_server.lib.storage_watcher.time.sleep', _sleep
            ),
            pytest.raises(_Stop),
        ):
            storage_watcher._watch_loop(mock.MagicMock(), '/data')

        assert sleeps == [storage_watcher.ERROR_BACKOFF_S]
