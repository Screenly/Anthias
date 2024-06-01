#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import unicode_literals
from future import standard_library
standard_library.install_aliases()
from builtins import str
from past.builtins import basestring
__author__ = "Screenly, Inc"
__copyright__ = "Copyright 2012-2023, Screenly, Inc"
__license__ = "Dual License: GPLv2 and Commercial License"

import json
import re
import sh
import shutil
import time
import os

import traceback
import yaml
import uuid
from celery import Celery
from datetime import datetime, timedelta
from dateutil import parser as date_parser
from functools import wraps
from mimetypes import guess_type
from os import getenv, listdir, makedirs, mkdir, path, remove, rename, stat, walk
from retry.api import retry_call

from flask import Flask, escape, make_response, request, send_from_directory, url_for, jsonify
from flask_cors import CORS
from flask_restful_swagger_2 import Api, Resource, Schema, swagger
from flask_swagger_ui import get_swaggerui_blueprint

from gunicorn.app.base import Application
from werkzeug.wrappers import Request

from lib import assets_helper
from lib import db
from lib import diagnostics
from lib import queries

from lib.auth import authorized

from lib.utils import (
    download_video_from_youtube, json_dump,
    get_node_ip,
    get_video_duration,
    is_balena_app, is_demo_node,
    shutdown_via_balena_supervisor, reboot_via_balena_supervisor,
    string_to_bool,
    connect_to_redis,
    url_fails,
    validate_url,
)

from settings import LISTEN, PORT, settings

HOME = getenv('HOME')
CELERY_RESULT_BACKEND = getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_BROKER_URL = getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_TASK_RESULT_EXPIRES = timedelta(hours=6)

app = Flask(__name__)
app.debug = string_to_bool(os.getenv('DEBUG', 'False'))

CORS(app)
api = Api(app, api_version="v1", title="Screenly OSE API")

r = connect_to_redis()
celery = Celery(
    app.name,
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


################################
# Utilities
################################


@api.representation('application/json')
def output_json(data, code, headers=None):
    response = make_response(json_dump(data), code)
    response.headers.extend(headers or {})
    return response


def api_error(error):
    return make_response(json_dump({'error': error}), 500)


################################
# API
################################

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


def prepare_default_asset(**kwargs):
    if kwargs['mimetype'] not in ['image', 'video', 'webpage']:
        return

    asset_id = 'default_{}'.format(uuid.uuid4().hex)
    duration = int(get_video_duration(kwargs['uri']).total_seconds()) if "video" == kwargs['mimetype'] else kwargs['duration']

    return {
        'asset_id': asset_id,
        'duration': duration,
        'end_date': kwargs['end_date'],
        'is_active': 1,
        'is_enabled': True,
        'is_processing': 0,
        'mimetype': kwargs['mimetype'],
        'name': kwargs['name'],
        'nocache': 0,
        'play_order': 0,
        'skip_asset_check': 0,
        'start_date': kwargs['start_date'],
        'uri': kwargs['uri']
    }


def add_default_assets():
    settings.load()

    datetime_now = datetime.now()
    default_asset_settings = {
        'start_date': datetime_now,
        'end_date': datetime_now.replace(year=datetime_now.year + 6),
        'duration': settings['default_duration']
    }

    default_assets_yaml = path.join(HOME, '.screenly/default_assets.yml')

    with open(default_assets_yaml, 'r') as yaml_file:
        default_assets = yaml.safe_load(yaml_file).get('assets')
        with db.conn(settings['database']) as conn:
            for default_asset in default_assets:
                default_asset_settings.update({
                    'name': default_asset.get('name'),
                    'uri': default_asset.get('uri'),
                    'mimetype': default_asset.get('mimetype')
                })
                asset = prepare_default_asset(**default_asset_settings)
                if asset:
                    assets_helper.create(conn, asset)


def remove_default_assets():
    settings.load()
    with db.conn(settings['database']) as conn:
        for asset in assets_helper.read(conn):
            if asset['asset_id'].startswith('default_'):
                assets_helper.delete(conn, asset['asset_id'])


# api view decorator. handles errors
def api_response(view):
    @wraps(view)
    def api_view(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except Exception as e:
            traceback.print_exc()
            return api_error(str(e))

    return api_view


@app.route('/upgrade_status/<task_id>')
def upgrade_screenly_status(task_id):
    status_code = 200
    task = upgrade_screenly.AsyncResult(task_id)
    if task.state == 'PENDING':
        response = {
            'state': task.state,
            'status': ''
        }
        status_code = 202
    elif task.state == 'PROGRESS':
        response = {
            'state': task.state,
            'status': task.info.get('status', '')
        }
        status_code = 202
    elif task.state != 'FAILURE':
        response = {
            'state': task.state,
            'status': task.info.get('status', '')
        }
    else:
        response = {
            'state': task.state,
            'status': str(task.info)
        }
    return jsonify(response), status_code


try:
    my_ip = get_node_ip()
except Exception:
    pass
else:
    SWAGGER_URL = '/api/docs'
    swagger_address = getenv("SWAGGER_HOST", my_ip)

    if settings['use_ssl'] or is_demo_node:
        API_URL = 'http://{}/api/swagger.json'.format(swagger_address)
    elif LISTEN == '127.0.0.1' or swagger_address != my_ip:
        API_URL = "http://{}/api/swagger.json".format(swagger_address)
    else:
        API_URL = "http://{}:{}/api/swagger.json".format(swagger_address, PORT)

    swaggerui_blueprint = get_swaggerui_blueprint(
        SWAGGER_URL,
        API_URL,
        config={
            'app_name': "Screenly OSE API"
        }
    )
    app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)


################################
# Static
################################


@app.context_processor
def override_url_for():
    return dict(url_for=dated_url_for)


def dated_url_for(endpoint, **values):
    if endpoint == 'static':
        filename = values.get('filename', None)
        if filename:
            file_path = path.join(app.root_path,
                                  endpoint, filename)
            if path.isfile(file_path):
                values['q'] = int(stat(file_path).st_mtime)
    return url_for(endpoint, **values)


@app.route('/static_with_mime/<string:path>')
@authorized
def static_with_mime(path):
    mimetype = request.args['mime'] if 'mime' in request.args else 'auto'
    return send_from_directory(directory='static', filename=path, mimetype=mimetype)


@app.before_first_request
def main():
    # Make sure the asset folder exist. If not, create it
    if not path.isdir(settings['assetdir']):
        mkdir(settings['assetdir'])
    # Create config dir if it doesn't exist
    if not path.isdir(settings.get_configdir()):
        makedirs(settings.get_configdir())

    with db.conn(settings['database']) as conn:
        with db.cursor(conn) as cursor:
            cursor.execute(queries.exists_table)
            if cursor.fetchone() is None:
                cursor.execute(assets_helper.create_assets_table)


def is_development():
    return getenv('FLASK_ENV', '') == 'development'


if __name__ == "__main__" and not is_development():
    config = {
        'bind': '{}:{}'.format(LISTEN, PORT),
        'threads': 2,
        'timeout': 20
    }

    class GunicornApplication(Application):
        def init(self, parser, opts, args):
            return config

        def load(self):
            return app

    GunicornApplication().run()
