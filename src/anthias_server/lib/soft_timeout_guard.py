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
from types import FrameType
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import task_postrun, task_prerun

logger = logging.getLogger(__name__)

# True only between ``task_prerun`` and ``task_postrun`` in this
# process. Celery sends ``task_postrun`` from the tracer's ``finally``,
# so a task that raised clears the flag just like one that returned.
_task_running = False


def _on_task_prerun(**_kwargs: Any) -> None:
    global _task_running
    _task_running = True


def _on_task_postrun(**_kwargs: Any) -> None:
    global _task_running
    _task_running = False


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
    # holds a reference to this module.
    task_prerun.connect(_on_task_prerun, weak=False)
    task_postrun.connect(_on_task_postrun, weak=False)
    billiard.pool.soft_timeout_sighandler = guarded_soft_timeout_sighandler
