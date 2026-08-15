"""Background watcher that keeps the under-voltage latch current.

The ``raspberrypi-hwmon`` driver re-reads the firmware every 2
seconds and clears the sticky bits each time, so
``in0_lcrit_alarm`` only reflects the last couple of seconds. A
periodic sampler on any sane interval would therefore walk straight
past a brown-out: a dip at ``t=3s`` is long gone by the time any
sanely-spaced beat task looks. That matters because the failure mode
we are trying to surface (a marginal power supply under load) is
often a burst of short dips rather than a sustained condition.

The driver calls ``hwmon_notify_event()`` whenever the alarm state
changes, which raises ``POLLPRI`` on the sysfs attribute. So instead
of sampling we block in :func:`select.poll` and get woken on every
transition, catching dips a sampler would miss while costing no CPU
in between. The poll also has a long timeout, a backstop re-sample
that re-syncs the latch if an event were ever missed. Events fire on
both edges, so nothing routine depends on it.

Runs in the celery worker: it is a single long-lived process on both
docker-compose installs and Balena fleets, and it already owns the
device's other periodic background work. The thread is a daemon so
it never holds up worker shutdown.
"""

from __future__ import annotations

import logging
import os
import select
import threading
import time
from typing import Any

from anthias_common import undervoltage

logger = logging.getLogger(__name__)

# Backstop only. Every state change arrives as a kernel POLLPRI
# notification, on both edges, so this timeout exists purely to
# re-sync if one were ever missed. It does not need to be anywhere
# near the resolution of the events themselves: a short interval
# would just wake the worker and re-read sysfs to learn nothing,
# hundreds of times a day, on a device that is behaving.
#
# The UI never depends on this cadence either. ``get_state`` reads the
# live attribute on every page render and writes back on disagreement,
# so what an operator sees is current to the request, not to the last
# poll.
RESAMPLE_INTERVAL_S = 3600

# Backoff after an unexpected failure (sysfs read error, driver
# unbound). Long enough not to spin, short enough that recovery is
# quick.
ERROR_BACKOFF_S = 60

# Conditions that mean the descriptor itself is unusable. Only
# meaningful when POLLPRI is absent, see the note in _watch_loop.
_POLL_ERROR_MASK = select.POLLERR | select.POLLHUP | select.POLLNVAL

_thread: threading.Thread | None = None
_lock = threading.Lock()


def _log_transition(active: bool, state: dict[str, Any]) -> None:
    if active:
        logger.warning(
            'Under-voltage detected on this device (occurrence %s since '
            'boot). The power supply is not delivering enough power; '
            'this can corrupt storage and cause display glitches.',
            state.get('count'),
        )
    else:
        logger.info('Under-voltage alarm cleared.')


def _watch_loop(alarm_path: str, redis_client: Any) -> None:
    previous: bool | None = None

    while True:
        try:
            # In steady state this fd is held open indefinitely: the
            # inner loop never exits normally. The reopen exists for
            # the recovery path only, so that a driver unbind/rebind
            # (which leaves the old fd reporting nothing) gets a fresh
            # descriptor after the backoff below, rather than leaving
            # a watcher that has quietly gone blind.
            with open(alarm_path) as alarm_file:
                poller = select.poll()
                poller.register(alarm_file, select.POLLPRI | select.POLLERR)

                # sysfs latches an initial POLLPRI that only clears
                # once the attribute has been read, so the first
                # read is required or poll() returns immediately
                # forever.
                alarm_file.read()

                while True:
                    events = poller.poll(RESAMPLE_INTERVAL_S * 1000)
                    mask = 0
                    for _fd, event in events:
                        mask |= event

                    # An empty list is the timeout, a normal re-sample.
                    #
                    # POLLERR alone means the device went away under us
                    # (driver unbound, hwmon removed) and would
                    # otherwise spin, since poll() then returns
                    # instantly for as long as the fd stays in that
                    # state. But POLLERR is NOT on its own an error
                    # here: kernfs_generic_poll() returns
                    # ``DEFAULT_POLLMASK | EPOLLERR | EPOLLPRI`` on
                    # every genuine change notification, so a real
                    # under-voltage event arrives as POLLPRI|POLLERR
                    # (measured as mask 0xa on a Pi 4, kernel 6.18).
                    # Treating that as fatal would raise on every
                    # brown-out, drop the transition, and back off for
                    # 60s: strictly worse than not using poll() at all.
                    # Hence the standard sysfs idiom, wake on POLLPRI
                    # and only believe POLLERR in its absence.
                    if mask & _POLL_ERROR_MASK and not mask & select.POLLPRI:
                        raise OSError(
                            f'poll() reported an error on {alarm_path} '
                            f'(mask 0x{mask:x})'
                        )

                    alarm_file.seek(0)
                    raw = alarm_file.read().strip()

                    # Treat an empty read as an error, not as "0".
                    # A removed sysfs attribute can read empty rather
                    # than raising, and mapping that to False would
                    # clear a live warning: the same trap
                    # ``read_alarm`` avoids by returning None rather
                    # than False for an unreadable attribute.
                    if not raw:
                        raise OSError(f'empty read from {alarm_path}')

                    active = raw == '1'

                    state = undervoltage.record_observation(
                        redis_client, active
                    )
                    if active != previous:
                        # ``previous is None`` is the first reading of
                        # this pass, not a transition. Logging it
                        # unconditionally made a perfectly healthy Pi
                        # report "Under-voltage alarm cleared." on the
                        # first re-sample after every boot, which reads
                        # as though an alarm had happened and cleared.
                        # A device that starts up already in alarm is
                        # still worth a line, so only the healthy-start
                        # case is suppressed.
                        if active or previous is not None:
                            _log_transition(active, state)
                        previous = active
        except Exception:
            logger.exception(
                'Under-voltage watcher failed; retrying in %ss.',
                ERROR_BACKOFF_S,
            )
            previous = None
            time.sleep(ERROR_BACKOFF_S)

            if not os.path.exists(alarm_path):
                # Bypass the cache: a driver unbind/rebind is the one
                # case where the path really can move, and the cached
                # value is precisely what is now stale.
                resolved = undervoltage.find_alarm_path(use_cache=False)
                if resolved is None:
                    logger.warning(
                        'Under-voltage sensor is no longer present; '
                        'watcher stopping.'
                    )
                    return
                alarm_path = resolved


def start(redis_client: Any) -> bool:
    """Start the watcher thread. Returns whether it was started.

    A no-op on boards without the ``rpi_volt`` driver (x86, most
    non-Pi arm64 SBCs). There is nothing to watch, and the UI stays
    silent rather than claiming a healthy supply it cannot verify.
    Idempotent, so a worker restart in-process won't stack threads.
    """
    global _thread

    with _lock:
        if _thread is not None and _thread.is_alive():
            return True

        alarm_path = undervoltage.find_alarm_path()
        if alarm_path is None:
            logger.info(
                'No rpi_volt hwmon sensor on this device; under-voltage '
                'monitoring is unavailable.'
            )
            return False

        # Seed the latch immediately so the UI is correct without
        # waiting for the first transition.
        try:
            initial = undervoltage.read_alarm(alarm_path)
            if initial is not None:
                undervoltage.record_observation(redis_client, initial)
        except Exception:
            logger.exception('Could not seed the under-voltage latch.')

        _thread = threading.Thread(
            target=_watch_loop,
            args=(alarm_path, redis_client),
            name='undervoltage-watcher',
            daemon=True,
        )
        _thread.start()
        logger.info('Watching %s for under-voltage events.', alarm_path)
        return True
