#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import unicode_literals
from future import standard_library
standard_library.install_aliases()
__author__ = "Screenly, Inc"
__copyright__ = "Copyright 2012-2023, Screenly, Inc"
__license__ = "Dual License: GPLv2 and Commercial License"

import re
import sh
import shutil
import time

import yaml
import uuid
from celery import Celery
from datetime import datetime, timedelta
from mimetypes import guess_type
from os import getenv, listdir, path, walk
from retry.api import retry_call

from lib import assets_helper, db, diagnostics
from lib.utils import (
    get_video_duration,
    is_balena_app,
    shutdown_via_balena_supervisor,
    reboot_via_balena_supervisor,
    connect_to_redis,
)

from settings import settings


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


def prepare_usb_asset(filepath, **kwargs):
    filetype = guess_type(filepath)[0]

    if not filetype:
        return

    filetype = filetype.split('/')[0]

    if filetype not in ['image', 'video']:
        return

    asset_id = uuid.uuid4().hex
    asset_name = path.basename(filepath)
    duration = int(get_video_duration(filepath).total_seconds()) if "video" == filetype else int(kwargs['duration'])

    if kwargs['copy']:
        shutil.copy(filepath, path.join(settings['assetdir'], asset_id))
        filepath = path.join(settings['assetdir'], asset_id)

    return {
        'asset_id': asset_id,
        'duration': duration,
        'end_date': kwargs['end_date'],
        'is_active': 1,
        'is_enabled': kwargs['activate'],
        'is_processing': 0,
        'mimetype': filetype,
        'name': asset_name,
        'nocache': 0,
        'play_order': 0,
        'skip_asset_check': 0,
        'start_date': kwargs['start_date'],
        'uri': filepath,
    }


################################
# Celery tasks
################################

@celery.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    # Calls cleanup() every hour.
    sender.add_periodic_task(3600, cleanup.s(), name='cleanup')
    sender.add_periodic_task(3600, cleanup_usb_assets.s(), name='cleanup_usb_assets')
    sender.add_periodic_task(60*5, get_display_power.s(), name='display_power')


@celery.task
def get_display_power():
    r.set('display_power', diagnostics.get_display_power())
    r.expire('display_power', 3600)


@celery.task
def cleanup():
    sh.find(path.join(HOME, 'screenly_assets'), '-name', '*.tmp', '-delete')


@celery.task
def reboot_screenly():
    """
    Background task to reboot Screenly-OSE.
    """
    if is_balena_app():
        retry_call(reboot_via_balena_supervisor, tries=5, delay=1)
    else:
        r.publish('hostcmd', 'reboot')


@celery.task
def shutdown_screenly():
    """
    Background task to shutdown Screenly-OSE.
    """
    if is_balena_app():
        retry_call(shutdown_via_balena_supervisor, tries=5, delay=1)
    else:
        r.publish('hostcmd', 'shutdown')


@celery.task
def append_usb_assets(mountpoint):
    """
    @TODO. Fix me. This will not work in Docker.
    """
    settings.load()

    datetime_now = datetime.now()
    usb_assets_settings = {
        'activate': False,
        'copy': False,
        'start_date': datetime_now,
        'end_date': datetime_now + timedelta(days=7),
        'duration': settings['default_duration']
    }

    for root, _, filenames in walk(mountpoint):
        if 'usb_assets_key.yaml' in filenames:
            with open("%s/%s" % (root, 'usb_assets_key.yaml'), 'r') as yaml_file:
                usb_file_settings = yaml.load(yaml_file, Loader=yaml.Loader).get('screenly')
                if usb_file_settings.get('key') == settings['usb_assets_key']:
                    if usb_file_settings.get('activate'):
                        usb_assets_settings.update({
                            'activate': usb_file_settings.get('activate')
                        })
                    if usb_file_settings.get('copy'):
                        usb_assets_settings.update({
                            'copy': usb_file_settings.get('copy')
                        })
                    if usb_file_settings.get('start_date'):
                        ts = time.mktime(datetime.strptime(usb_file_settings.get('start_date'), "%m/%d/%Y").timetuple())
                        usb_assets_settings.update({
                            'start_date': datetime.utcfromtimestamp(ts)
                        })
                    if usb_file_settings.get('end_date'):
                        ts = time.mktime(datetime.strptime(usb_file_settings.get('end_date'), "%m/%d/%Y").timetuple())
                        usb_assets_settings.update({
                            'end_date': datetime.utcfromtimestamp(ts)
                        })
                    if usb_file_settings.get('duration'):
                        usb_assets_settings.update({
                            'duration': usb_file_settings.get('duration')
                        })

                    files = ['%s/%s' % (root, y) for root, _, filenames in walk(mountpoint) for y in filenames]
                    with db.conn(settings['database']) as conn:
                        for filepath in files:
                            asset = prepare_usb_asset(filepath, **usb_assets_settings)
                            if asset:
                                assets_helper.create(conn, asset)

                    break


@celery.task
def remove_usb_assets(mountpoint):
    """
    @TODO. Fix me. This will not work in Docker.
    """
    settings.load()
    with db.conn(settings['database']) as conn:
        for asset in assets_helper.read(conn):
            if asset['uri'].startswith(mountpoint):
                assets_helper.delete(conn, asset['asset_id'])


@celery.task
def cleanup_usb_assets(media_dir='/media'):
    """
    @TODO. Fix me. This will not work in Docker.
    """
    settings.load()
    mountpoints = ['%s/%s' % (media_dir, x) for x in listdir(media_dir) if path.isdir('%s/%s' % (media_dir, x))]
    with db.conn(settings['database']) as conn:
        for asset in assets_helper.read(conn):
            if asset['uri'].startswith(media_dir):
                location = re.search(r'^(/\w+/\w+[^/])', asset['uri'])
                if location:
                    if location.group() not in mountpoints:
                        assets_helper.delete(conn, asset['asset_id'])
