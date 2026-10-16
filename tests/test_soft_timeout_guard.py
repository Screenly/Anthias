"""Tests for the celery soft-timeout signal guard.

The behaviour under test is the one that keeps an idle prefork child
alive when a late ``SIGUSR1`` arrives for a job that has already
finished (Sentry ANTHIAS-B / ANTHIAS-1Q / ANTHIAS-45).
"""

from __future__ import annotations

import signal
from collections.abc import Iterator
from typing import Any, cast
from unittest import mock

import billiard.pool
import pytest
from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import (
    task_failure,
    task_postrun,
    task_prerun,
    task_retry,
    task_success,
)

from anthias_server.lib import soft_timeout_guard


@pytest.fixture(autouse=True)
def _restore_billiard_handler() -> Iterator[None]:
    """Leave ``billiard.pool`` exactly as the test found it."""
    original = billiard.pool.soft_timeout_sighandler
    try:
        yield
    finally:
        billiard.pool.soft_timeout_sighandler = original


@pytest.fixture(autouse=True)
def _idle_between_tests() -> Iterator[None]:
    """Clear the running-task flag either side of every test."""
    soft_timeout_guard._on_task_finished()
    try:
        yield
    finally:
        soft_timeout_guard._on_task_finished()


def test_signal_during_a_task_still_raises() -> None:
    """A real soft limit must reach the task's own handler."""
    soft_timeout_guard._on_task_prerun()

    with pytest.raises(SoftTimeLimitExceeded):
        soft_timeout_guard.guarded_soft_timeout_sighandler(
            signal.SIGUSR1, None
        )


def test_signal_while_idle_is_discarded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The case that used to kill the child: no task body running.

    ``workloop`` has no try/except around ``wait_for_job()``, so before
    this guard the raise propagated out of ``Worker.__call__`` and the
    child exited 1.
    """
    with caplog.at_level('WARNING', logger=soft_timeout_guard.__name__):
        soft_timeout_guard.guarded_soft_timeout_sighandler(
            signal.SIGUSR1, None
        )

    assert 'already finished' in caplog.text


def test_postrun_clears_the_flag_after_a_failed_task() -> None:
    """Celery sends ``task_postrun`` from a ``finally``.

    So a task that raised must leave the child just as disarmed as one
    that returned -- otherwise the first failure would re-arm the old
    child-killing behaviour for the rest of that child's life.
    """
    soft_timeout_guard._on_task_prerun()
    soft_timeout_guard._on_task_finished()

    # Raising here would be the regression; pytest fails the test on
    # any exception escaping the call.
    soft_timeout_guard.guarded_soft_timeout_sighandler(signal.SIGUSR1, None)


@pytest.mark.parametrize(
    'outcome', [task_success, task_failure, task_retry, task_postrun]
)
def test_every_outcome_signal_disarms_the_handler(outcome: Any) -> None:
    """The flag must be down before teardown, on every exit path.

    ``task_postrun`` is the last signal of the tracer's ``-* POST *-``
    block, so clearing only there leaves the handler armed across
    ``Task.after_return`` and any postrun receiver connected ahead of
    this module's. A signal landing in that window has no body to
    interrupt, and would turn a task that already succeeded into a
    reported internal error.

    Each outcome is dispatched here the way celery dispatches it --
    through the signal, to the receivers ``install()`` connected --
    rather than by calling the receiver directly, so the test fails if
    a signal is ever dropped from the ``install()`` loop.
    """
    soft_timeout_guard.install()
    soft_timeout_guard._on_task_prerun()

    outcome.send(sender=None)

    assert soft_timeout_guard._task_running is False
    # Raising here is the defect under test.
    soft_timeout_guard.guarded_soft_timeout_sighandler(signal.SIGUSR1, None)


def test_a_signal_arriving_during_teardown_is_discarded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The window a postrun-only guard leaves open.

    Replays the tracer's ``-* POST *-`` order: the body returns,
    ``task_success`` fires, and only later does ``task_postrun``. The
    gap between them is ``Task.after_return`` plus every postrun
    receiver connected ahead of this module's -- real work, during
    which a postrun-only guard still has the flag up.

    SIGUSR1 delivered in that gap has no body to interrupt. Under the
    old arrangement it raised, and the tracer's ``except Exception``
    turned a task that had already succeeded into a reported internal
    error.
    """
    soft_timeout_guard.install()
    soft_timeout_guard._on_task_prerun()

    task_success.send(sender=None)

    with caplog.at_level('WARNING', logger=soft_timeout_guard.__name__):
        # Raising here is the defect; pytest fails on anything escaping.
        soft_timeout_guard.guarded_soft_timeout_sighandler(
            signal.SIGUSR1, None
        )

    task_postrun.send(sender=None)

    assert 'already finished' in caplog.text


def test_install_replaces_the_billiard_handler() -> None:
    def _stand_in_for_billiards_own(signum: int, frame: Any) -> None: ...

    billiard.pool.soft_timeout_sighandler = _stand_in_for_billiards_own

    soft_timeout_guard.install()

    assert (
        billiard.pool.soft_timeout_sighandler
        is soft_timeout_guard.guarded_soft_timeout_sighandler
    )


def test_install_is_idempotent() -> None:
    soft_timeout_guard.install()

    with mock.patch.object(task_prerun, 'connect') as connect:
        soft_timeout_guard.install()

    connect.assert_not_called()


def test_install_connects_a_clearer_to_every_outcome_signal() -> None:
    """Pins the set, so a new exit path is not silently left armed."""
    soft_timeout_guard.install()

    for outcome in (task_success, task_failure, task_retry, task_postrun):
        assert soft_timeout_guard._on_task_finished in [
            receiver for _, receiver in outcome.receivers
        ], outcome


def test_forked_child_installs_the_guarded_handler() -> None:
    """The load-bearing claim: ``after_fork`` picks up our rebinding.

    ``Worker.after_fork`` resolves ``soft_timeout_sighandler`` on the
    module at call time, *after* running the pool initializer -- which
    is why the guard cannot be installed from a
    ``worker_process_init`` receiver. This asserts the child really
    ends up with our handler armed on ``SIG_SOFT_TIMEOUT``.
    """
    soft_timeout_guard.install()

    class _Child:
        """Just the attributes ``after_fork`` touches."""

        inq = object()
        outq = object()
        initializer = None
        initargs = ()
        sigprotection = False

    installed: dict[int, Any] = {}

    def _record(signum: int, handler: Any) -> None:
        installed[signum] = handler

    with (
        mock.patch.object(billiard.pool, 'reset_signals'),
        mock.patch('signal.signal', _record),
    ):
        billiard.pool.Worker.after_fork(cast(billiard.pool.Worker, _Child()))

    assert (
        installed[billiard.pool.SIG_SOFT_TIMEOUT]
        is soft_timeout_guard.guarded_soft_timeout_sighandler
    )
