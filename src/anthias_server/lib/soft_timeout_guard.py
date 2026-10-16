"""Stop a late celery soft-timeout signal from killing a pool child.

Celery arms the soft limit against the *job slot*, not the task body,
and the parent decides to fire it from a timer that only knows whether
the job is still in ``Pool._cache``. A job leaves that cache when the
parent drains the child's result off the outqueue -- which is a
separate step from the child finishing the work. On a memory-pressured
board the parent's event loop lags (soft limits observed firing 113s
after a 30s timer was armed), so the gap between "child finished and
went back to ``wait_for_job()``" and "parent noticed" is seconds wide.

Inside that gap ``asynpool._on_soft_timeout`` still finds the job in
the cache and sends ``SIGUSR1`` to a child that is now idle.
``billiard.pool.soft_timeout_sighandler`` raises ``SoftTimeLimitExceeded``
unconditionally, so the exception lands in the idle ``workloop``, where
no task handler can ever catch it. It propagates out of
``Worker.__call__``, the child exits 1, and billiard's parent logs

    Process 'ForkPoolWorker-N' pid:M exited with 'exitcode 1'

at ERROR -- which is Sentry ANTHIAS-B, the largest issue in the
project, with the respawn churn it causes showing up as ANTHIAS-1Q
("Timed out waiting for UP message from ForkPoolWorker-N"). The
escaping exception itself was ANTHIAS-45, whose traceback is the
evidence for all of the above:

    billiard/pool.py:292  in __call__   sys.exit(self.workloop(pid=pid))
    billiard/pool.py:351  in workloop   req = wait_for_job()
    ...
    billiard/pool.py:228  in soft_timeout_sighandler
                                        raise SoftTimeLimitExceeded()

#3352 taught ``before_send`` to drop that report, which silenced the
symptom but left the child dying. This module fixes the cause: the
signal is only honoured while a task body is actually running in this
process, and a signal that arrives with nothing to interrupt is logged
and discarded.

Nothing is lost by discarding it. A soft limit exists to interrupt a
running task; with no task running there is nothing to cut short, and
the hard limit remains the backstop for a task genuinely wedged in C
code where the soft signal cannot be delivered.

The guard has to be installed by *replacing the module attribute* on
``billiard.pool``, not from a ``worker_process_init`` receiver:
``Worker.after_fork`` runs the pool initializer (which is what sends
``worker_process_init``) and only *then* calls
``signal.signal(SIG_SOFT_TIMEOUT, soft_timeout_sighandler)``, so a
handler installed from that receiver is immediately overwritten.
Because ``after_fork`` resolves the name on the module at call time,
rebinding it before the pool forks is enough, and every child inherits
it.

Prefork-specific by construction: the flag is a plain module global
because a prefork child runs one task body at a time on its main
thread, which is also the only thread CPython delivers signals on.
"""

from __future__ import annotations

import logging
from time import monotonic
from types import FrameType
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import (
    task_failure,
    task_postrun,
    task_prerun,
    task_retry,
    task_success,
)

logger = logging.getLogger(__name__)

# True only while a task body is on the stack in this process.
#
# Cleared on the earliest signal each outcome reaches, not on
# ``task_postrun``. The tracer runs, in order: the body, then
# ``task_success`` or ``task_failure``/``task_retry``, then
# ``Task.after_return``, then ``task_postrun``
# (``celery/app/trace.py``, the ``-* POST *-`` block). Clearing at
# ``task_postrun`` alone would leave the flag set across
# ``after_return`` and every postrun receiver connected ahead of this
# one -- a body-less window in which the handler would still raise.
#
# That raise could not kill the child: ``send_postrun`` sits inside the
# tracer's ``try``, whose ``except Exception`` turns anything escaping
# teardown into ``report_internal_error``. But it would convert a task
# that had already succeeded into a reported internal error, so the
# window is worth closing rather than reasoning around.
#
# ``task_postrun`` stays connected as the backstop: it is the one
# signal every outcome reaches, including ``Ignore``, which celery
# raises without a dedicated signal.
_task_running = False

# What the running body's own soft limit implies for an arriving
# signal, as a pair: the ``monotonic()`` deadline it falls due at, and
# whether the limit was resolved at all.
#
# A signal carries no job identity -- billiard sends a bare
# ``SIGUSR1`` -- so "a task is running" does not mean the signal was
# armed for *that* task. A child can finish job A, leave its result on
# the outqueue, and pick job B off the inq before the parent has
# drained A from ``Pool._cache``; A's late timer then fires at a child
# that is busy with B, and interrupting B would abort a task whose own
# limit has not expired.
#
# The one job-correlated fact the child does hold is how long the
# current body has been running against the limit that body was
# dispatched with. The parent arms the timer at handover, so a
# legitimate signal cannot arrive before the running body has reached
# its own limit; one that does belongs to an earlier job.
#
# The three states are distinct and must not be collapsed:
#
# * a deadline -- honour the signal once it is reached, drop it before;
# * resolved to *no* soft limit -- celery passed ``soft_timeout=None``
#   for this job, so no timer was ever armed for it and any soft
#   timeout signal arriving now belongs to an earlier one. Several
#   tasks are in this state until #3385 gives them soft limits;
# * unresolved -- fall open and honour the signal. Suppressing a real
#   soft limit would hand the task to the hard limit instead: a
#   SIGKILL with no ``on_failure``, which is the failure mode #3385
#   exists to remove.
_task_deadline: float | None = None
_task_limit_resolved = False

# Covers the skew between the parent arming the timer and this child
# reaching ``task_prerun`` (pickling, inq transit, the tracer's
# preamble). Only has to outlast that handover, so it is small next to
# any real limit -- the shortest in this project is 90s. Erring high
# costs only that a stale signal landing within five seconds of the
# running body's own deadline is still honoured; erring low would risk
# dropping a real limit.
_SOFT_LIMIT_SKEW_S = 5.0


def _soft_limit_deadline(task: Any) -> tuple[float | None, bool]:
    """When the body now starting reaches its own soft limit.

    Returns the deadline and whether the limit was resolved, so the
    caller can tell "this task has no soft limit" from "the limit
    could not be read" -- the first is grounds for dropping a signal,
    the second is grounds for honouring it.

    Resolved the way celery resolves the limit it arms the timer with
    (``worker/request.py``, ``execute_using_pool``): the per-call
    ``timelimit`` on the request, then the task's own
    ``soft_time_limit``, then the worker-wide default.
    """
    try:
        request_limits = getattr(task.request, 'timelimit', None)
        soft = (request_limits or (None, None))[1]
        soft = (
            soft or task.soft_time_limit or task.app.conf.task_soft_time_limit
        )
        return (monotonic() + float(soft) if soft else None), True
    except Exception:
        logger.debug(
            'Could not resolve a soft time limit for the running task; '
            'the guard will honour any soft timeout signal it gets.',
            exc_info=True,
        )
        return None, False


def _on_task_prerun(task: Any = None, **_kwargs: Any) -> None:
    global _task_running, _task_deadline, _task_limit_resolved
    _task_deadline, _task_limit_resolved = _soft_limit_deadline(task)
    _task_running = True


def _on_task_finished(**_kwargs: Any) -> None:
    global _task_running, _task_deadline, _task_limit_resolved
    _task_running = False
    _task_deadline = None
    _task_limit_resolved = False


def _is_for_an_earlier_job() -> bool:
    """Whether a signal arriving now cannot be for the running body."""
    if not _task_limit_resolved:
        return False
    if _task_deadline is None:
        # No soft limit on this job, so the parent never armed a timer
        # for it. Whatever this signal is, it is not this task's.
        return True
    return monotonic() < _task_deadline - _SOFT_LIMIT_SKEW_S


def guarded_soft_timeout_sighandler(
    signum: int, frame: FrameType | None
) -> None:
    """Raise the soft limit only when there is a task body to interrupt.

    Drop-in replacement for ``billiard.pool.soft_timeout_sighandler``,
    down to the parameter names: the assignment in :func:`install` is
    only type-compatible with billiard's own signature if they match.
    """
    del signum, frame  # a signal handler's arguments, unused here

    if _task_running:
        if _is_for_an_earlier_job():
            # Armed for a job this child has already finished and the
            # parent has not yet drained. Interrupting the body that
            # happens to be running now would abort the wrong task.
            logger.warning(
                'Discarded a soft time limit signal that arrived while a '
                'task was running that the signal cannot belong to; it '
                'was armed for an earlier job. This would otherwise have '
                'interrupted the wrong task.'
            )
            return

        raise SoftTimeLimitExceeded()

    # Warning, not exception: this is a diagnosable condition rather
    # than a fault, and at WARNING it reaches journald without the
    # Sentry logging integration (ERROR and above) filing an event --
    # which would just reintroduce the noise this change removes.
    logger.warning(
        'Discarded a soft time limit signal that arrived with no task '
        'running; the job it was armed for had already finished. This '
        'would otherwise have killed the pool child.'
    )


def install() -> None:
    """Arm the guard for this process and every pool child it forks.

    Idempotent, and safe to call from a process that never starts a
    pool (the server imports the task module to enqueue work): it only
    rebinds an attribute and connects two signal receivers.
    """
    import billiard.pool

    if (
        billiard.pool.soft_timeout_sighandler
        is guarded_soft_timeout_sighandler
    ):
        return

    # ``weak=False`` so the receivers survive regardless of what else
    # holds a reference to this module. ``task_success`` is only
    # dispatched when it has receivers at tracer-build time, which
    # connecting here -- before the worker builds any tracer --
    # guarantees.
    task_prerun.connect(_on_task_prerun, weak=False)
    for finished in (task_success, task_failure, task_retry, task_postrun):
        finished.connect(_on_task_finished, weak=False)
    billiard.pool.soft_timeout_sighandler = guarded_soft_timeout_sighandler
