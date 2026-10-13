"""One-shot WAL assertion for the shared SQLite file.

``journal_mode`` is a property of the database *file*, not of a
connection: once a database is in WAL it stays in WAL across restarts,
restores and processes. Asserting it per connection — which is what
putting ``PRAGMA journal_mode=WAL`` in the Django ``init_command`` does
— is therefore redundant on every connection but the one that actually
converts, and on a file that is *not* yet in WAL it is actively harmful:

* switching a database into WAL takes an exclusive lock, so under the
  three processes that share this file (uvicorn, the celery worker, the
  viewer) the pragma blocks for the full busy timeout and then raises
  ``OperationalError: database is locked``;
* it raises from inside ``get_new_connection``, i.e. *before* Django
  owns the connection, so every request on that worker 500s and the
  half-built sqlite handle is dropped without ``close()`` — a leaked fd
  per attempt, which is how a device that cannot convert goes from
  "slow page" to ``OSError: [Errno 24] Too many open files``
  (Sentry ANTHIAS-38 → ANTHIAS-62/63, one device, one afternoon).

So the conversion belongs somewhere a failure is survivable. This
module does it once per process at startup, on its own short-lived
connection, and treats a refusal as "stay on the rollback journal for
now and try again next boot" rather than as a fault: with the 20s busy
timeout the settings module also configures, a non-WAL database is
slower under contention but entirely usable, which is the right trade
against serving errors.
"""

import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

# Busy-wait budget for the conversion attempt. Deliberately far shorter
# than the 20s connection timeout: nothing downstream depends on the
# conversion succeeding *now*, and this runs at startup where a long
# block would delay the service rather than queue behind it.
WAL_CONVERSION_TIMEOUT_S = 5.0


def ensure_wal_journal_mode(db_path: str | None) -> str | None:
    """Put the SQLite database at ``db_path`` into WAL, best effort.

    Returns the journal mode in force afterwards (lowercased), or
    ``None`` if it could not be determined — a contended database, a
    path that cannot be opened, or an in-memory database, none of which
    are errors from the caller's point of view.

    Already-WAL is the common case and costs one read-only pragma: the
    conversion is only attempted when the file says it is in some other
    mode, so a healthy fleet never takes the exclusive lock at all.
    """
    if not db_path or db_path == ':memory:' or 'mode=memory' in db_path:
        return None
    # ``sqlite3.connect`` creates a missing file. Before the first
    # migrate that would hand ``start_server.sh`` an empty database and
    # send it down the existing-database branch; a fresh install's
    # first service start converts it instead.
    if not os.path.isfile(db_path):
        return None

    try:
        conn = sqlite3.connect(db_path, timeout=WAL_CONVERSION_TIMEOUT_S)
    except sqlite3.Error:
        # A missing parent directory, a read-only filesystem, a corrupt
        # header. Whatever it is, the real connection that follows will
        # report it with the context to act on, and the common case here
        # is mundane — a management command run anywhere but a device
        # has no ``/data`` at all. Debug, so neither the device logs nor
        # a developer's terminal carry a traceback for it.
        logger.debug(
            'Could not open %s to check its journal mode',
            db_path,
            exc_info=True,
        )
        return None

    current = None
    try:
        current = _journal_mode(conn)
        if current == 'wal':
            return current
        row = conn.execute('PRAGMA journal_mode=WAL').fetchone()
        applied = str(row[0]).lower() if row else None
        if applied != 'wal':
            # SQLite answers a refused journal-mode change with the mode
            # still in force rather than an error, so this branch is a
            # real outcome and not dead code.
            logger.warning(
                'Database %s stayed on journal mode %r instead of WAL; '
                'continuing with the busy timeout as the only concurrency '
                'guard',
                db_path,
                applied or current,
            )
        return applied
    except sqlite3.Error:
        logger.warning(
            'Could not switch %s to WAL journal mode (still %r); '
            'continuing — the next startup retries',
            db_path,
            current,
            exc_info=True,
        )
        return current
    finally:
        # Explicit: an abandoned connection holds its fds until the GC
        # gets to it, which is the failure mode this module exists to
        # avoid.
        conn.close()


def _journal_mode(conn: sqlite3.Connection) -> str | None:
    row = conn.execute('PRAGMA journal_mode').fetchone()
    return str(row[0]).lower() if row else None
