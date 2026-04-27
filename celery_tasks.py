import logging
from datetime import timedelta
from os import getenv, path
from typing import Any

import django
import sh
from celery import Celery
from tenacity import Retrying, stop_after_attempt, wait_fixed

try:
    django.setup()

    # Place imports that uses Django in this block.

    from lib import diagnostics
    from lib.utils import (
        connect_to_redis,
        is_balena_app,
        reboot_via_balena_supervisor,
        shutdown_via_balena_supervisor,
    )
except Exception:
    pass


__author__ = 'Screenly, Inc'
__copyright__ = 'Copyright 2012-2026, Screenly, Inc'
__license__ = 'Dual License: GPLv2 and Commercial License'


CELERY_RESULT_BACKEND = getenv(
    'CELERY_RESULT_BACKEND', 'redis://localhost:6379/0'
)
CELERY_BROKER_URL = getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_TASK_RESULT_EXPIRES = timedelta(hours=6)

r = connect_to_redis()
celery = Celery(
    'Anthias Celery Worker',
    backend=CELERY_RESULT_BACKEND,
    broker=CELERY_BROKER_URL,
    result_expires=CELERY_TASK_RESULT_EXPIRES,
)


@celery.on_after_configure.connect
def setup_periodic_tasks(sender: Any, **kwargs: Any) -> None:
    # Calls cleanup() every hour.
    sender.add_periodic_task(3600, cleanup.s(), name='cleanup')
    sender.add_periodic_task(
        60 * 5, get_display_power.s(), name='display_power'
    )


@celery.task(time_limit=30)
def get_display_power() -> None:
    r.set('display_power', diagnostics.get_display_power())
    r.expire('display_power', 3600)


@celery.task
def cleanup() -> None:
    # Without HOME, `path.join(..., 'anthias_assets')` would be a
    # relative path and `find -delete` could chew through whatever
    # directory celery happens to be running in. Bail out instead.
    home = getenv('HOME')
    if not home:
        logging.error('cleanup() skipped: HOME is not set')
        return
    sh.find(
        path.join(home, 'anthias_assets'),
        '-name',
        '*.tmp',
        '-delete',
    )


@celery.task
def reboot_anthias() -> None:
    """
    Background task to reboot Anthias
    """
    if is_balena_app():
        for attempt in Retrying(
            stop=stop_after_attempt(5),
            wait=wait_fixed(1),
        ):
            with attempt:
                reboot_via_balena_supervisor()
    else:
        r.publish('hostcmd', 'reboot')


@celery.task
def shutdown_anthias() -> None:
    """
    Background task to shutdown Anthias
    """
    if is_balena_app():
        for attempt in Retrying(
            stop=stop_after_attempt(5),
            wait=wait_fixed(1),
        ):
            with attempt:
                shutdown_via_balena_supervisor()
    else:
        r.publish('hostcmd', 'shutdown')
