#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import unicode_literals
from future import standard_library
standard_library.install_aliases()

import sh

from celery import Celery
from datetime import timedelta
from os import getenv, path
from retry.api import retry_call

from lib import diagnostics
from lib.utils import (
    is_balena_app,
    shutdown_via_balena_supervisor,
    reboot_via_balena_supervisor,
    connect_to_redis,
)


__author__ = "Screenly, Inc"
__copyright__ = "Copyright 2012-2024, Screenly, Inc"
__license__ = "Dual License: GPLv2 and Commercial License"


HOME = getenv('HOME')
CELERY_RESULT_BACKEND = getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_BROKER_URL = getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_TASK_RESULT_EXPIRES = timedelta(hours=6)


r = connect_to_redis()
celery = Celery(
    'Anthias Celery Worker',
    backend=CELERY_RESULT_BACKEND,
    broker=CELERY_BROKER_URL,
    result_expires=CELERY_TASK_RESULT_EXPIRES
)


################################
# Celery tasks
################################

@celery.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    # Calls cleanup() every hour.
    sender.add_periodic_task(3600, cleanup.s(), name='cleanup')
    sender.add_periodic_task(60*5, get_display_power.s(), name='display_power')


@celery.task
def get_display_power():
    r.set('display_power', diagnostics.get_display_power())
    r.expire('display_power', 3600)


@celery.task
def cleanup():
    sh.find(path.join(HOME, 'screenly_assets'), '-name', '*.tmp', '-delete')


@celery.task
def reboot_anthias():
    """
    Background task to reboot Anthias.
    """
    if is_balena_app():
        retry_call(reboot_via_balena_supervisor, tries=5, delay=1)
    else:
        r.publish('hostcmd', 'reboot')


@celery.task
def shutdown_anthias():
    """
    Background task to shutdown Anthias.
    """
    if is_balena_app():
        retry_call(shutdown_via_balena_supervisor, tries=5, delay=1)
    else:
        r.publish('hostcmd', 'shutdown')
