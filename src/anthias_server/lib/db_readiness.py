"""Is the shared SQLite schema ready to be queried?

Three containers open the same ``~/.anthias/anthias.db``: anthias-server,
which owns the startup ``migrate`` pass in ``bin/start_server.sh``, plus
anthias-celery and anthias-viewer, which only read and write rows. The
latter two start in parallel with the server, so both can reach the
database before a single table exists, and any query made in that window
dies with ``OperationalError: no such table: assets``.

Neither of them can infer the answer from a side channel. "The server
answers HTTP" does not mean "the schema is in place": uvicorn is
``exec``'d after the migrate pass, but that pass can fall through to
``dbrestore`` (which drops and re-creates every table) and the HTTP
listener comes up either way. The only honest question is the one
``manage.py migrate --check`` asks, which is what ``migrations_ready``
asks here -- directly, with no database of its own to carry.

Imports stay Django-only on purpose: ``anthias-viewer`` installs the
``viewer`` dependency group, which ships no celery, and
``tests/test_viewer_import_closure.py`` fails the build if a module the
viewer imports pulls celery/billiard/kombu in at module level.
"""

import logging
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Poll cadence while a container waits for the server's startup
# ``migrate`` pass to finish, and how often a waiting container repeats
# its log line.
MIGRATION_WAIT_POLL_S = 5
MIGRATION_WAIT_LOG_EVERY_S = 30


def migrations_ready() -> bool:
    """True when the shared SQLite schema is fully migrated.

    Computes the same unapplied-migration plan ``manage.py migrate
    --check`` does. A database-side error getting there
    (``OperationalError: database is locked`` while the server's
    dbbackup/dbrestore holds the file, a missing ``django_migrations``
    table on a first-boot/empty DB) means the schema is not ready
    either, so report not-ready rather than raising; the underlying
    error is kept visible at DEBUG for runs with ``--loglevel=debug``.
    Anything that is NOT a database error (a programming bug in the
    probe itself) propagates so the caller fails fast instead of
    waiting forever.
    """
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor
    from django.db.utils import DatabaseError

    connection = connections['default']
    try:
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    except DatabaseError:
        logger.debug(
            'Migration-readiness probe failed; treating as not ready',
            exc_info=True,
        )
        return False
    finally:
        # Don't leak the probe connection into forked children —
        # SQLite handles must not be shared across fork().
        connection.close()
    return not plan


def wait_for_migrations(
    *,
    description: str,
    log: logging.Logger,
    ready: Callable[[], bool] | None = None,
    on_poll: Callable[[], None] | None = None,
) -> None:
    """Block until ``ready()`` reports the schema is in place.

    Deliberately unbounded: a container without a schema can do no
    useful work, and the alternative -- give up after a fixed budget
    and query anyway -- is what files the crash reports this function
    exists to stop. A long first-boot ``migrate`` on a slow SD card is
    a wait, not a failure. The log line below keeps it observable.

    ``on_poll`` runs once per poll for callers that must keep something
    alive while they wait (the viewer pokes its watchdog file).
    """
    if ready is None:
        ready = migrations_ready
    waited = 0
    next_log_at = 0
    while not ready():
        # First miss logs immediately, then repeat every
        # MIGRATION_WAIT_LOG_EVERY_S — a multi-minute migrate/restore
        # on a slow SD card shouldn't drown the device log in a
        # warning per poll. Tracked as an explicit next-log threshold
        # so the cadence holds whatever the two constants are set to.
        if waited >= next_log_at:
            log.warning(
                'Database is not migrated yet; delaying %s (%ss elapsed; '
                'probe errors are logged at DEBUG)',
                description,
                waited,
            )
            next_log_at = waited + MIGRATION_WAIT_LOG_EVERY_S
        if on_poll is not None:
            on_poll()
        time.sleep(MIGRATION_WAIT_POLL_S)
        waited += MIGRATION_WAIT_POLL_S
