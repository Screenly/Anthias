import logging
from os import path, utime
from time import sleep

import requests

from lib.errors import SigalrmError
from settings import LISTEN, PORT
from viewer.media_player import MediaPlayerProxy

WATCHDOG_PATH = '/tmp/screenly.watchdog'


def sigalrm(signum, frame):
    """
    Signal just throw an SigalrmError
    """
    raise SigalrmError("SigalrmError")


def sigusr1(signum, frame):
    """
    The signal interrupts sleep() calls, so the currently
    playing web or image asset is skipped.
    """
    logging.info('USR1 received, skipping.')
    MediaPlayerProxy.get_instance().stop()


def command_not_found():
    logging.error("Command not found")


def watchdog():
    """Notify the watchdog file to be used with the watchdog-device."""
    if not path.isfile(WATCHDOG_PATH):
        open(WATCHDOG_PATH, 'w').close()
    else:
        utime(WATCHDOG_PATH, None)


def wait_for_server(retries, wt=1):
    for _ in range(retries):
        try:
            response = requests.get(f'http://{LISTEN}:{PORT}/splash-page')
            response.raise_for_status()
            break
        except requests.exceptions.RequestException:
            sleep(wt)
