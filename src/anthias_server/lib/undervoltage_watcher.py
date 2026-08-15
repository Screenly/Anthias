"""Background watcher that keeps the under-voltage latch current.

The ``raspberrypi-hwmon`` driver re-reads the firmware every 2
seconds and clears the sticky bits each time, so
``in0_lcrit_alarm`` only reflects the last couple of seconds. A
periodic sampler on any sane interval would therefore walk straight
past a brown-out: a dip at ``t=3s`` is gone by the time a 30-second
beat task looks at ``t=30s``. That matters because the failure mode
we are trying to surface (a marginal power supply under load) is
often a burst of short dips rather than a sustained condition.

The driver calls ``hwmon_notify_event()`` whenever the alarm state
changes, which raises ``POLLPRI`` on the sysfs attribute. So instead
of sampling we block in :func:`select.poll` and get woken on every
transition, catching dips a sampler would miss while costing no CPU
in between. The poll also has a timeout, which doubles as a
re-sample so the latch stays fresh (and ``active`` de-escalates
correctly) even if an event is ever missed.

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

# Wake up at least this often even with no state change, to refresh
# the latch and correct ``active`` if an event was missed.
RESAMPLE_INTERVAL_S = 30

# Backoff after an unexpected failure (sysfs read error, driver
# unbound). Long enough not to spin, short enough that recovery is
# quick.
ERROR_BACKOFF_S = 60

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
            # Reopened each pass rather than held forever: if the
            # driver is unbound and rebound (or the path changes
            # across a probe reorder) a stale fd would silently
            # stop reporting, and a watcher that has quietly gone
            # blind is worse than no watcher.
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

                    # An empty list is the timeout, which is a normal
                    # re-sample. POLLERR means the device went away
                    # under us (driver unbound, hwmon removed) and
                    # would otherwise spin: poll() returns instantly
                    # every iteration for as long as the fd stays in
                    # that state. Break out to the reopen path.
                    if any(
                        event
                        & (select.POLLERR | select.POLLHUP | select.POLLNVAL)
                        for _fd, event in events
                    ):
                        raise OSError(
                            f'poll() reported an error on {alarm_path}'
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
                        # report "Under-voltage alarm cleared." ~30s
                        # after every boot, which reads as though an
                        # alarm had happened and cleared. A device
                        # that starts up already in alarm is still
                        # worth a line, so only the healthy-start case
                        # is suppressed.
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
