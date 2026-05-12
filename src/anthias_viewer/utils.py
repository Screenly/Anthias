import logging
import threading
from os import path, utime
from time import sleep
from types import FrameType
from typing import Any

import requests

from anthias_common.errors import SigalrmError
from anthias_server.settings import LISTEN, PORT

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
    logging.error('Command not found')


def watchdog() -> None:
    """Notify the watchdog file to be used with the watchdog-device."""
    if not path.isfile(WATCHDOG_PATH):
        open(WATCHDOG_PATH, 'w').close()
    else:
        utime(WATCHDOG_PATH, None)


def wait_for_server(retries: int, wt: int = 1) -> None:
    for _ in range(retries):
        try:
            response = requests.get(f'http://{LISTEN}:{PORT}/splash-page')
            response.raise_for_status()
            break
        except requests.exceptions.RequestException:
            sleep(wt)
