"""Block a service's startup until the shared schema is migrated.

anthias-server owns the database: its start script runs a dbbackup ->
migrate pass, with a dbrestore fallback that drops and re-creates every
table. The other containers start in parallel with that, so anything
that queries a model during the window dies with ``OperationalError: no
such table: assets``.

Celery hit this first (Sentry ANTHIAS-1) and grew the wait below as a
``worker_init`` receiver. The viewer has the same exposure and no
guard: ``main()`` builds its ``Scheduler`` as soon as
``wait_for_server`` returns, which happens either when the server
answers ``/splash-page`` or when that poll quietly gives up after its
timeout -- and ``/splash-page`` answering says nothing about a
dbrestore that is still dropping tables. The first thing the
``Scheduler`` does is ``generate_asset_list()``, so it reads ``assets``
and the process dies on the way up (Sentry ANTHIAS-C, "Viewer
crashed.").

Both callers want exactly the same thing, so the probe and the wait
live here rather than in either service.
"""

from __future__ import annotations

import logging
import time
from typing import Final

logger = logging.getLogger(__name__)

POLL_INTERVAL_S: Final = 5

# A multi-minute migrate/restore on a slow SD card should not drown the
# device log in a line per poll.
LOG_EVERY_S: Final = 30


def migrations_ready() -> bool:
    """True when the shared schema is fully migrated.

    Computes the same unapplied-migration plan ``manage.py migrate
    --check`` does. A database-side error getting there
    (``OperationalError: database is locked`` while the server's
    dbbackup/dbrestore holds the file, a missing ``django_migrations``
    table on a first-boot/empty DB) means the schema is not ready
    either, so report not-ready rather than raising; the underlying
    error is kept visible at DEBUG. Anything that is NOT a database
    error (a programming bug in the probe itself) propagates so the
    caller fails fast instead of waiting forever.
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
        # Don't leak the probe connection into a process that is about
        # to fork (celery's prefork pool) -- SQLite handles must not be
        # shared across fork().
        connection.close()
    return not plan


def wait_for_migrations(what: str) -> None:
    """Poll until :func:`migrations_ready`, logging the wait.

    Deliberately unbounded: a service without a database can do no
    useful work, and a bounded wait is what leaves the caller querying
    a half-restored schema in the first place. ``what`` names the
    service being delayed so the device log says which one is waiting.
    """
    waited = 0
    next_log_at = 0
    while not migrations_ready():
        # First miss logs immediately, then every LOG_EVERY_S. Tracked
        # as an explicit next-log threshold so the cadence holds
        # whatever the two constants are set to.
        if waited >= next_log_at:
            logger.warning(
                'Database is not migrated yet; delaying %s startup '
                '(%ss elapsed; probe errors are logged at DEBUG)',
                what,
                waited,
            )
            next_log_at = waited + LOG_EVERY_S
        time.sleep(POLL_INTERVAL_S)
        waited += POLL_INTERVAL_S
