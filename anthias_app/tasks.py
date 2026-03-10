import logging
import threading
from os import getenv, path

import sh
from tenacity import Retrying, stop_after_attempt, wait_fixed

from lib import diagnostics
from lib.utils import (
    is_balena_app,
    reboot_via_balena_supervisor,
    shutdown_via_balena_supervisor,
)

logger = logging.getLogger(__name__)

_display_power = None
_scheduler_started = False
_scheduler_lock = threading.Lock()


def get_display_power_value() -> str | bool | None:
    return _display_power


def _update_display_power() -> None:
    global _display_power
    try:
        _display_power = diagnostics.get_display_power()
    except Exception:
        logger.exception('Failed to get display power')


def cleanup() -> None:
    try:
        asset_dir = path.join(getenv('HOME', ''), 'screenly_assets')
        if path.isdir(asset_dir):
            sh.find(asset_dir, '-name', '*.tmp', '-delete')
    except Exception:
        logger.exception('Cleanup failed')


def reboot_anthias() -> None:
    if is_balena_app():
        for attempt in Retrying(
            stop=stop_after_attempt(5),
            wait=wait_fixed(1),
        ):
            with attempt:
                reboot_via_balena_supervisor()
    else:
        from subprocess import call

        call(['reboot'])


def shutdown_anthias() -> None:
    if is_balena_app():
        for attempt in Retrying(
            stop=stop_after_attempt(5),
            wait=wait_fixed(1),
        ):
            with attempt:
                shutdown_via_balena_supervisor()
    else:
        from subprocess import call

        call(['shutdown', '-h', 'now'])


def _run_periodic(
    func, interval_seconds: int, name: str,
) -> threading.Thread:
    def loop():
        while True:
            try:
                func()
            except Exception:
                logger.exception('Periodic task %s failed', name)
            threading.Event().wait(interval_seconds)

    t = threading.Thread(target=loop, name=name, daemon=True)
    t.start()
    return t


def start_background_scheduler() -> None:
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    logger.info('Starting background task scheduler')
    _run_periodic(cleanup, 3600, 'cleanup')
    _run_periodic(_update_display_power, 300, 'display_power')
