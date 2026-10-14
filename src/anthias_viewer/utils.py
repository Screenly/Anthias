import logging
import threading
from os import path, utime
from time import sleep
from types import FrameType
from typing import Any

import requests

from anthias_common.errors import SigalrmError
from anthias_server.lib import db_readiness
from anthias_server.settings import LISTEN, PORT

logger = logging.getLogger(__name__)

WATCHDOG_PATH = '/tmp/anthias.watchdog'


def sigalrm(signum: int, frame: FrameType | None) -> None:
    """
    Signal just throw an SigalrmError
    """
    raise SigalrmError('SigalrmError')


def get_skip_event() -> threading.Event:
    """
    Get the global skip event for instant asset switching.
    """
    from anthias_viewer.playback import skip_event

    return skip_event


def command_not_found(*args: Any, **kwargs: Any) -> None:
    logger.error('Command not found')


def watchdog() -> None:
    """Notify the watchdog file to be used with the watchdog-device."""
    if not path.isfile(WATCHDOG_PATH):
        open(WATCHDOG_PATH, 'w').close()
    else:
        utime(WATCHDOG_PATH, None)


def wait_for_server(retries: int, wt: int = 1) -> None:
    """Poll anthias-server's splash page until it answers.

    Bounded, and falling through when the budget runs out is
    deliberate: the viewer's own display pipeline does not need the
    server, so a server that never comes up should not keep the screen
    blank. It is logged rather than silent, because every later failure
    on a device whose server never started reads very differently once
    you know this timed out.
    """
    for _ in range(retries):
        try:
            response = requests.get(f'http://{LISTEN}:{PORT}/splash-page')
            response.raise_for_status()
            return
        except requests.exceptions.RequestException:
            sleep(wt)
    logger.warning(
        'anthias-server did not answer within %ss; continuing without it',
        retries * wt,
    )


def wait_for_db_schema() -> None:
    """Block until the database the scheduler reads has its tables.

    ``wait_for_server`` is not this gate, in either of its outcomes. It
    gives up after its budget and returns anyway, and even when it
    succeeds, ``/splash-page`` answering only means uvicorn is
    listening — the start script can reach that point via the
    ``dbrestore`` fallback, which drops and re-creates every table
    underneath it.

    Querying through that window is what crash-loops the viewer: the
    first thing ``Scheduler()`` does is ``generate_asset_list()``, which
    dies with ``OperationalError: no such table: assets``, and
    ``__main__`` re-raises so the container restarts and does it all
    again (Sentry ANTHIAS-C). The schema is the thing being waited for,
    so ask about the schema.

    The watchdog file is touched on every poll. A first-boot migrate on
    a slow SD card can outlast the host watchdog's patience, and a
    viewer that is correctly waiting must not look like a wedged one.
    """
    db_readiness.wait_for_migrations(
        description='viewer startup',
        log=logger,
        on_poll=watchdog,
    )
