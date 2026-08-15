"""Background watcher that keeps the storage-health latch current.

The under-voltage watcher blocks in ``poll()`` because its sensor
raises ``POLLPRI`` on every transition. Nothing here does that: ext4
updates its superblock counters silently, and a read-only remount
raises no event at all. So this one samples.

That is fine, because unlike a brown-out, none of these signals are
transient. A read-only filesystem stays read-only until someone
reboots, an error count only ever goes up, and wear moves over
months. Nothing is missed by looking every minute instead of being
told.

Two cadences, because the two probes cost different things:

* The sysfs counters are three small reads with no side effects, so
  they run every ``SAMPLE_INTERVAL_S``.
* The write check actually writes to the card, so it runs every
  ``WRITE_CHECK_INTERVAL_S``. A page every quarter of an hour is
  nothing against what SQLite and the container logs already do, but
  it is not free either, and running it on the sample loop would be
  wear spent for no extra information.

Runs in the celery worker for the same reasons as the under-voltage
watcher: one long-lived process on both docker-compose installs and
Balena fleets, already the home of the device's periodic background
work. A daemon thread rather than a beat task specifically because
of ``fsync`` -- on a failing card it can block for tens of seconds,
which would tie up a worker slot and eventually trip the task's soft
time limit mid-write. On its own thread a slow ``fsync`` costs
nothing but its own lateness, and the duration is itself a signal
worth recording.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from anthias_common import storage_health

logger = logging.getLogger(__name__)

# Cheap sysfs reads. Fast enough that the UI reflects a read-only
# remount within a minute of it happening.
SAMPLE_INTERVAL_S = 60

# Actually writes to the card. See the module docstring.
WRITE_CHECK_INTERVAL_S = 900

# Backoff after an unexpected failure, long enough not to spin.
ERROR_BACKOFF_S = 300

_thread: threading.Thread | None = None
_lock = threading.Lock()


def _log_status(status: str, state: dict[str, Any]) -> None:
    """One log line per status change, aimed at whoever reads the
    device logs during a support call rather than at the UI."""
    if status == storage_health.STATUS_FAILING:
        logger.warning(
            'Storage on %s is failing (read_only=%s, write=%s, ext4 '
            'errors=%s). The memory card is no longer reliable and '
            'changes may not be saved.',
            state.get('device'),
            state.get('read_only'),
            state.get('write_reason') or 'ok',
            state.get('errors_count'),
        )
    elif status == storage_health.STATUS_FULL:
        logger.warning(
            'Storage on %s is full; writes are failing with ENOSPC.',
            state.get('device'),
        )
    elif status == storage_health.STATUS_ERRORS:
        logger.warning(
            'Storage on %s has %s filesystem error(s) recorded in its '
            'superblock (most recent: %s). Writes are working now.',
            state.get('device'),
            state.get('errors_count'),
            state.get('last_error') or 'unknown',
        )
    elif status == storage_health.STATUS_WEAR:
        logger.warning(
            'Storage on %s is nearing end of life (wear=%s%%, pre_eol=%s).',
            state.get('device'),
            state.get('media', {}).get('wear_pct'),
            state.get('media', {}).get('pre_eol'),
        )
    elif status == storage_health.STATUS_UNKNOWN:
        # Not healthy: we could not resolve the filesystem at all
        # (mountinfo or sysfs unreadable). Logging "healthy again"
        # here would state the opposite of what happened.
        logger.warning(
            'Storage health for %s is unknown; the filesystem could '
            'not be resolved.',
            state.get('device'),
        )
    else:
        logger.info('Storage on %s is healthy again.', state.get('device'))


def _watch_loop(redis_client: Any, data_dir: str) -> None:
    previous: str | None = None
    # Negative so the first pass always includes a write check: the
    # UI should be right from startup rather than a quarter of an
    # hour later.
    last_write_check: float = -WRITE_CHECK_INTERVAL_S

    while True:
        try:
            now = time.monotonic()
            write_check = now - last_write_check >= WRITE_CHECK_INTERVAL_S

            state = storage_health.record_check(
                redis_client, data_dir, write_check=write_check
            )
            # Gated on ``supported`` because that is the same
            # condition record_check applies before actually writing:
            # asking for a check the filesystem could not be resolved
            # for is a no-op, and stamping the interval for it would
            # push the next real check up to WRITE_CHECK_INTERVAL_S
            # past recovery.
            #
            # Stamped after the call, not before: on a card that is
            # struggling the check itself can take a while, and the
            # interval should be a gap between checks rather than a
            # deadline they overrun.
            if write_check and state['supported']:
                last_write_check = time.monotonic()

            status = state['status']
            if status != previous:
                _log_status(status, state)
                previous = status

            time.sleep(SAMPLE_INTERVAL_S)
        except Exception:
            logger.exception(
                'Storage-health watcher failed; retrying in %ss.',
                ERROR_BACKOFF_S,
            )
            previous = None
            time.sleep(ERROR_BACKOFF_S)


def start(redis_client: Any, data_dir: str | None = None) -> bool:
    """Start the watcher thread. Returns whether it was started.

    A no-op when the data directory's filesystem cannot be resolved
    at all, which in practice means a container without
    ``/proc/self/mountinfo``. Idempotent, so a worker restart
    in-process won't stack threads.
    """
    global _thread

    if data_dir is None:
        data_dir = storage_health.default_data_dir()

    with _lock:
        if _thread is not None and _thread.is_alive():
            return True

        facts = storage_health.probe(data_dir)
        if not facts['supported']:
            logger.info(
                'Could not resolve the filesystem behind %s; storage '
                'health monitoring is unavailable.',
                data_dir,
            )
            return False

        _thread = threading.Thread(
            target=_watch_loop,
            args=(redis_client, data_dir),
            name='storage-health-watcher',
            daemon=True,
        )
        _thread.start()
        logger.info(
            'Watching %s (%s on %s) for storage errors.',
            data_dir,
            facts['fstype'],
            facts['device'],
        )
        return True
