import logging
import os
import time
from datetime import timedelta
from os import getenv, path
from typing import Any

import django
import sh
from celery import Celery
from tenacity import Retrying, stop_after_attempt, wait_fixed

django.setup()

# Place imports that uses Django in this block.

from anthias_app.models import Asset  # noqa: E402
from lib import diagnostics  # noqa: E402
from lib.utils import (  # noqa: E402
    connect_to_redis,
    is_balena_app,
    reboot_via_balena_supervisor,
    shutdown_via_balena_supervisor,
)
from settings import settings  # noqa: E402


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
    asset_dir = settings['assetdir']
    if not path.isdir(asset_dir):
        return

    # Stale upload remnants: in-progress uploads write to <uuid>.tmp and
    # rename on commit. The 1h mtime guard avoids killing an upload that
    # is still streaming when celery beat fires.
    sh.find(
        asset_dir,
        '-name',
        '*.tmp',
        '-type',
        'f',
        '-mmin',
        '+60',
        '-delete',
    )

    # Orphaned asset files: forum 6636 / GH #2657. Asset rows can be
    # deleted while their file lingers (e.g. URI didn't match assetdir
    # exactly, or the file was renamed by an upgrade). Sweep anything
    # in assetdir that no live Asset row references, with the same 1h
    # guard so a freshly-renamed file isn't removed before its row is
    # written.
    #
    # Skip suffixes that belong to in-flight uploads (.tmp) and yt-dlp's
    # active download bookkeeping (.part, .ytdl, .info.json) so a slow
    # YouTube fetch isn't reaped mid-transfer.
    skip_suffixes = ('.tmp', '.part', '.ytdl', '.info.json')
    referenced = {
        path.basename(uri)
        for uri in Asset.objects.exclude(uri__isnull=True)
        .exclude(uri__exact='')
        .values_list('uri', flat=True)
        if uri and uri.startswith(asset_dir)
    }
    cutoff = 60 * 60  # match the .tmp guard above
    now = time.time()
    for entry in os.scandir(asset_dir):
        if not entry.is_file():
            continue
        if entry.name in referenced or entry.name.endswith(skip_suffixes):
            continue
        try:
            if now - entry.stat().st_mtime < cutoff:
                continue
            os.remove(entry.path)
        except OSError as e:
            logging.warning('cleanup: could not remove %s: %s', entry.path, e)


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
