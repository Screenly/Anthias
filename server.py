#!/usr/bin/env python
# -*- coding: utf-8 -*-

__author__ = "Screenly, Inc"
__copyright__ = "Copyright 2012-2021, Screenly, Inc"
__license__ = "Dual License: GPLv2 and Commercial License"

import json
import pydbus
import psutil
import re
import sh
import shutil
import time
import os

import traceback
import yaml
import uuid
from base64 import b64encode
from celery import Celery
from datetime import datetime, timedelta
from dateutil import parser as date_parser
from functools import wraps
from hurry.filesize import size
from mimetypes import guess_type, guess_extension
from os import getenv, listdir, makedirs, mkdir, path, remove, rename, statvfs, stat, walk
from subprocess import check_output
from urlparse import urlparse

from flask import Flask, escape, make_response, render_template, request, send_from_directory, url_for, jsonify
from flask_cors import CORS
from flask_restful_swagger_2 import Api, Resource, Schema, swagger
from flask_swagger_ui import get_swaggerui_blueprint

from gunicorn.app.base import Application
from werkzeug.wrappers import Request

from lib import assets_helper
from lib import backup_helper
from lib import db
from lib import diagnostics
from lib import queries
from lib import raspberry_pi_helper

from lib.github import is_up_to_date
from lib.auth import authorized
from lib.utils import download_video_from_youtube, json_dump
from lib.utils import generate_perfect_paper_password, is_docker
from lib.utils import get_active_connections, remove_connection
from lib.utils import get_node_ip, get_node_mac_address
from lib.utils import get_video_duration
from lib.utils import is_balena_app, is_demo_node
from lib.utils import string_to_bool
from lib.utils import connect_to_redis
from lib.utils import url_fails
from lib.utils import validate_url

from settings import CONFIGURABLE_SETTINGS, DEFAULTS, LISTEN, PORT, settings, ZmqPublisher, ZmqCollector

HOME = getenv('HOME', '/home/pi')
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
    r.publish('hostcmd', 'reboot')


@celery.task
def shutdown_screenly():
    """
    Background task to shutdown Screenly-OSE.
    """
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
                usb_file_settings = yaml.load(yaml_file).get('screenly')
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


def template(template_name, **context):
    """Screenly template response generator. Shares the
    same function signature as Flask's render_template() method
    but also injects some global context."""

    # Add global contexts
    context['date_format'] = settings['date_format']
    context['default_duration'] = settings['default_duration']
    context['default_streaming_duration'] = settings['default_streaming_duration']
    context['template_settings'] = {
        'imports': ['from lib.utils import template_handle_unicode'],
        'default_filters': ['template_handle_unicode'],
    }
    context['up_to_date'] = is_up_to_date()
    context['use_24_hour_clock'] = settings['use_24_hour_clock']

    return render_template(template_name, context=context)


################################
# Models
################################

class AssetModel(Schema):
    type = 'object'
    properties = {
        'asset_id': {'type': 'string'},
        'name': {'type': 'string'},
        'uri': {'type': 'string'},
        'start_date': {
            'type': 'string',
            'format': 'date-time'
        },
        'end_date': {
            'type': 'string',
            'format': 'date-time'
        },
        'duration': {'type': 'string'},
        'mimetype': {'type': 'string'},
        'is_active': {
            'type': 'integer',
            'format': 'int64',
        },
        'is_enabled': {
            'type': 'integer',
            'format': 'int64',
        },
        'is_processing': {
            'type': 'integer',
            'format': 'int64',
        },
        'nocache': {
            'type': 'integer',
            'format': 'int64',
        },
        'play_order': {
            'type': 'integer',
            'format': 'int64',
        },
        'skip_asset_check': {
            'type': 'integer',
            'format': 'int64',
        }
    }


class AssetRequestModel(Schema):
    type = 'object'
    properties = {
        'name': {'type': 'string'},
        'uri': {'type': 'string'},
        'start_date': {
            'type': 'string',
            'format': 'date-time'
        },
        'end_date': {
            'type': 'string',
            'format': 'date-time'
        },
        'duration': {'type': 'string'},
        'mimetype': {'type': 'string'},
        'is_enabled': {
            'type': 'integer',
            'format': 'int64',
        },
        'nocache': {
            'type': 'integer',
            'format': 'int64',
        },
        'play_order': {
            'type': 'integer',
            'format': 'int64',
        },
        'skip_asset_check': {
            'type': 'integer',
            'format': 'int64',
        }
    }
    required = ['name', 'uri', 'mimetype', 'is_enabled', 'start_date', 'end_date']


class AssetContentModel(Schema):
    type = 'object'
    properties = {
        'type': {'type': 'string'},
        'url': {'type': 'string'},
        'filename': {'type': 'string'},
        'mimetype': {'type': 'string'},
        'content': {
            'type': 'string',
            'format': 'byte'
        },
    }
    required = ['type', 'filename']


class AssetPropertiesModel(Schema):
    type = 'object'
    properties = {
        'name': {'type': 'string'},
        'start_date': {
            'type': 'string',
            'format': 'date-time'
        },
        'end_date': {
            'type': 'string',
            'format': 'date-time'
        },
        'duration': {'type': 'string'},
        'is_active': {
            'type': 'integer',
            'format': 'int64',
        },
        'is_enabled': {
            'type': 'integer',
            'format': 'int64',
        },
        'nocache': {
            'type': 'integer',
            'format': 'int64',
        },
        'play_order': {
            'type': 'integer',
            'format': 'int64',
        },
        'skip_asset_check': {
            'type': 'integer',
            'format': 'int64',
        }
    }


################################
# API
################################

def prepare_asset(request, unique_name=False):
    req = Request(request.environ)
    data = None

    # For backward compatibility
    try:
        data = json.loads(req.data)
    except ValueError:
        data = json.loads(req.form['model'])
    except TypeError:
        data = json.loads(req.form['model'])

    def get(key):
        val = data.get(key, '')
        if isinstance(val, unicode):
            return val.strip()
        elif isinstance(val, basestring):
            return val.strip().decode('utf-8')
        else:
            return val

    if not all([get('name'), get('uri'), get('mimetype')]):
        raise Exception("Not enough information provided. Please specify 'name', 'uri', and 'mimetype'.")

    name = escape(get('name'))
    if unique_name:
        with db.conn(settings['database']) as conn:
            names = assets_helper.get_names_of_assets(conn)
        if name in names:
            i = 1
            while True:
                new_name = '%s-%i' % (name, i)
                if new_name in names:
                    i += 1
                else:
                    name = new_name
                    break

    asset = {
        'name': name,
        'mimetype': get('mimetype'),
        'asset_id': get('asset_id'),
        'is_enabled': get('is_enabled'),
        'is_processing': get('is_processing'),
        'nocache': get('nocache'),
    }

    uri = escape(get('uri').encode('utf-8'))

    if uri.startswith('/'):
        if not path.isfile(uri):
            raise Exception("Invalid file path. Failed to add asset.")
    else:
        if not validate_url(uri):
            raise Exception("Invalid URL. Failed to add asset.")

    if not asset['asset_id']:
        asset['asset_id'] = uuid.uuid4().hex
        if uri.startswith('/'):
            rename(uri, path.join(settings['assetdir'], asset['asset_id']))
            uri = path.join(settings['assetdir'], asset['asset_id'])

    if 'youtube_asset' in asset['mimetype']:
        uri, asset['name'], asset['duration'] = download_video_from_youtube(uri, asset['asset_id'])
        asset['mimetype'] = 'video'
        asset['is_processing'] = 1

    asset['uri'] = uri

    if "video" in asset['mimetype']:
        if get('duration') == 'N/A' or int(get('duration')) == 0:
            asset['duration'] = int(get_video_duration(uri).total_seconds())
    else:
        # Crashes if it's not an int. We want that.
        asset['duration'] = int(get('duration'))

    asset['skip_asset_check'] = int(get('skip_asset_check')) if int(get('skip_asset_check')) else 0

    # parse date via python-dateutil and remove timezone info
    if get('start_date'):
        asset['start_date'] = date_parser.parse(get('start_date')).replace(tzinfo=None)
    else:
        asset['start_date'] = ""

    if get('end_date'):
        asset['end_date'] = date_parser.parse(get('end_date')).replace(tzinfo=None)
    else:
        asset['end_date'] = ""

    return asset


def prepare_asset_v1_2(request_environ, asset_id=None, unique_name=False):
    data = json.loads(request_environ.data)

    def get(key):
        val = data.get(key, '')
        if isinstance(val, unicode):
            return val.strip()
        elif isinstance(val, basestring):
            return val.strip().decode('utf-8')
        else:
            return val

    if not all([get('name'),
                get('uri'),
                get('mimetype'),
                str(get('is_enabled')),
                get('start_date'),
                get('end_date')]):
        raise Exception(
            "Not enough information provided. Please specify 'name', 'uri', 'mimetype', 'is_enabled', 'start_date' and 'end_date'.")

    ampfix = "&amp;"
    name = escape(get('name').replace(ampfix, '&'))
    if unique_name:
        with db.conn(settings['database']) as conn:
            names = assets_helper.get_names_of_assets(conn)
        if name in names:
            i = 1
            while True:
                new_name = '%s-%i' % (name, i)
                if new_name in names:
                    i += 1
                else:
                    name = new_name
                    break

    asset = {
        'name': name,
        'mimetype': get('mimetype'),
        'is_enabled': get('is_enabled'),
        'nocache': get('nocache')
    }

    uri = (get('uri')).replace(ampfix, '&').replace('<', '&lt;').replace('>', '&gt;').replace('\'', '&apos;').replace('\"', '&quot;')

    if uri.startswith('/'):
        if not path.isfile(uri):
            raise Exception("Invalid file path. Failed to add asset.")
    else:
        if not validate_url(uri):
            raise Exception("Invalid URL. Failed to add asset.")

    if not asset_id:
        asset['asset_id'] = uuid.uuid4().hex

    if not asset_id and uri.startswith('/'):
        new_uri = "{}{}".format(path.join(settings['assetdir'], asset['asset_id']), get('ext'))
        rename(uri, new_uri)
        uri = new_uri

    if 'youtube_asset' in asset['mimetype']:
        uri, asset['name'], asset['duration'] = download_video_from_youtube(uri, asset['asset_id'])
        asset['mimetype'] = 'video'
        asset['is_processing'] = 1

    asset['uri'] = uri

    if "video" in asset['mimetype']:
        if get('duration') == 'N/A' or int(get('duration')) == 0:
            asset['duration'] = int(get_video_duration(uri).total_seconds())
    elif get('duration'):
        # Crashes if it's not an int. We want that.
        asset['duration'] = int(get('duration'))
    else:
        asset['duration'] = 10

    asset['play_order'] = get('play_order') if get('play_order') else 0

    asset['skip_asset_check'] = int(get('skip_asset_check')) if int(get('skip_asset_check')) else 0

    # parse date via python-dateutil and remove timezone info
    asset['start_date'] = date_parser.parse(get('start_date')).replace(tzinfo=None)
    asset['end_date'] = date_parser.parse(get('end_date')).replace(tzinfo=None)

    return asset


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


def update_asset(asset, data):
    for key, value in data.items():

        if key in ['asset_id', 'is_processing', 'mimetype', 'uri'] or key not in asset:
            continue

        if key in ['start_date', 'end_date']:
            value = date_parser.parse(value).replace(tzinfo=None)

        if key in ['play_order', 'skip_asset_check', 'is_enabled', 'is_active', 'nocache']:
            value = int(value)

        if key == 'duration':
            if "video" not in asset['mimetype']:
                continue
            value = int(value)

        asset.update({key: value})


# api view decorator. handles errors
def api_response(view):
    @wraps(view)
    def api_view(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except Exception as e:
            traceback.print_exc()
            return api_error(unicode(e))

    return api_view


class Assets(Resource):
    method_decorators = [authorized]

    @swagger.doc({
        'responses': {
            '200': {
                'description': 'List of assets',
                'schema': {
                    'type': 'array',
                    'items': AssetModel

                }
            }
        }
    })
    def get(self):
        with db.conn(settings['database']) as conn:
            assets = assets_helper.read(conn)
            return assets

    @api_response
    @swagger.doc({
        'parameters': [
            {
                'name': 'model',
                'in': 'formData',
                'type': 'string',
                'description':
                    '''
                    Yes, that is just a string of JSON not JSON itself it will be parsed on the other end.
                    Content-Type: application/x-www-form-urlencoded
                    model: "{
                        "name": "Website",
                        "mimetype": "webpage",
                        "uri": "http://example.com",
                        "is_active": 0,
                        "start_date": "2017-02-02T00:33:00.000Z",
                        "end_date": "2017-03-01T00:33:00.000Z",
                        "duration": "10",
                        "is_enabled": 0,
                        "is_processing": 0,
                        "nocache": 0,
                        "play_order": 0,
                        "skip_asset_check": 0
                    }"
                    '''
            }
        ],
        'responses': {
            '201': {
                'description': 'Asset created',
                'schema': AssetModel
            }
        }
    })
    def post(self):
        asset = prepare_asset(request)
        if url_fails(asset['uri']):
            raise Exception("Could not retrieve file. Check the asset URL.")
        with db.conn(settings['database']) as conn:
            return assets_helper.create(conn, asset), 201


class Asset(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'parameters': [
            {
                'name': 'asset_id',
                'type': 'string',
                'in': 'path',
                'description': 'id of an asset'
            }
        ],
        'responses': {
            '200': {
                'description': 'Asset',
                'schema': AssetModel
            }
        }
    })
    def get(self, asset_id):
        with db.conn(settings['database']) as conn:
            return assets_helper.read(conn, asset_id)

    @swagger.doc({
        'parameters': [
            {
                'name': 'asset_id',
                'type': 'string',
                'in': 'path',
                'description': 'id of an asset'
            },
            {
                'name': 'model',
                'in': 'formData',
                'type': 'string',
                'description':
                    '''
                    Content-Type: application/x-www-form-urlencoded
                    model: "{
                        "asset_id": "793406aa1fd34b85aa82614004c0e63a",
                        "name": "Website",
                        "mimetype": "webpage",
                        "uri": "http://example.com",
                        "is_active": 0,
                        "start_date": "2017-02-02T00:33:00.000Z",
                        "end_date": "2017-03-01T00:33:00.000Z",
                        "duration": "10",
                        "is_enabled": 0,
                        "is_processing": 0,
                        "nocache": 0,
                        "play_order": 0,
                        "skip_asset_check": 0
                    }"
                    '''
            }
        ],
        'responses': {
            '200': {
                'description': 'Asset updated',
                'schema': AssetModel
            }
        }
    })
    def put(self, asset_id):
        with db.conn(settings['database']) as conn:
            return assets_helper.update(conn, asset_id, prepare_asset(request))

    @swagger.doc({
        'parameters': [
            {
                'name': 'asset_id',
                'type': 'string',
                'in': 'path',
                'description': 'id of an asset'
            },
        ],
        'responses': {
            '204': {
                'description': 'Deleted'
            }
        }
    })
    def delete(self, asset_id):
        with db.conn(settings['database']) as conn:
            asset = assets_helper.read(conn, asset_id)
            try:
                if asset['uri'].startswith(settings['assetdir']):
                    remove(asset['uri'])
            except OSError:
                pass
            assets_helper.delete(conn, asset_id)
            return '', 204  # return an OK with no content


class AssetsV1_1(Resource):
    method_decorators = [authorized]

    @swagger.doc({
        'responses': {
            '200': {
                'description': 'List of assets',
                'schema': {
                    'type': 'array',
                    'items': AssetModel

                }
            }
        }
    })
    def get(self):
        with db.conn(settings['database']) as conn:
            assets = assets_helper.read(conn)
            return assets

    @api_response
    @swagger.doc({
        'parameters': [
            {
                'in': 'body',
                'name': 'model',
                'description': 'Adds a asset',
                'schema': AssetModel,
                'required': True
            }
        ],
        'responses': {
            '201': {
                'description': 'Asset created',
                'schema': AssetModel
            }
        }
    })
    def post(self):
        asset = prepare_asset(request, unique_name=True)
        if url_fails(asset['uri']):
            raise Exception("Could not retrieve file. Check the asset URL.")
        with db.conn(settings['database']) as conn:
            return assets_helper.create(conn, asset), 201


class AssetV1_1(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'parameters': [
            {
                'name': 'asset_id',
                'type': 'string',
                'in': 'path',
                'description': 'id of an asset'
            }
        ],
        'responses': {
            '200': {
                'description': 'Asset',
                'schema': AssetModel
            }
        }
    })
    def get(self, asset_id):
        with db.conn(settings['database']) as conn:
            return assets_helper.read(conn, asset_id)

    @swagger.doc({
        'parameters': [
            {
                'name': 'asset_id',
                'type': 'string',
                'in': 'path',
                'description': 'id of an asset',
                'required': True
            },
            {
                'in': 'body',
                'name': 'model',
                'description': 'Adds an asset',
                'schema': AssetModel,
                'required': True
            }
        ],
        'responses': {
            '200': {
                'description': 'Asset updated',
                'schema': AssetModel
            }
        }
    })
    def put(self, asset_id):
        with db.conn(settings['database']) as conn:
            return assets_helper.update(conn, asset_id, prepare_asset(request))

    @swagger.doc({
        'parameters': [
            {
                'name': 'asset_id',
                'type': 'string',
                'in': 'path',
                'description': 'id of an asset',
                'required': True

            },
        ],
        'responses': {
            '204': {
                'description': 'Deleted'
            }
        }
    })
    def delete(self, asset_id):
        with db.conn(settings['database']) as conn:
            asset = assets_helper.read(conn, asset_id)
            try:
                if asset['uri'].startswith(settings['assetdir']):
                    remove(asset['uri'])
            except OSError:
                pass
            assets_helper.delete(conn, asset_id)
            return '', 204  # return an OK with no content


class AssetsV1_2(Resource):
    method_decorators = [authorized]

    @swagger.doc({
        'responses': {
            '200': {
                'description': 'List of assets',
                'schema': {
                    'type': 'array',
                    'items': AssetModel
                }
            }
        }
    })
    def get(self):
        with db.conn(settings['database']) as conn:
            return assets_helper.read(conn)

    @api_response
    @swagger.doc({
        'parameters': [
            {
                'in': 'body',
                'name': 'model',
                'description': 'Adds an asset',
                'schema': AssetRequestModel,
                'required': True
            }
        ],
        'responses': {
            '201': {
                'description': 'Asset created',
                'schema': AssetModel
            }
        }
    })
    def post(self):
        request_environ = Request(request.environ)
        asset = prepare_asset_v1_2(request_environ, unique_name=True)
        if not asset['skip_asset_check'] and url_fails(asset['uri']):
            raise Exception("Could not retrieve file. Check the asset URL.")
        with db.conn(settings['database']) as conn:
            assets = assets_helper.read(conn)
            ids_of_active_assets = [x['asset_id'] for x in assets if x['is_active']]

            asset = assets_helper.create(conn, asset)

            if asset['is_active']:
                ids_of_active_assets.insert(asset['play_order'], asset['asset_id'])
            assets_helper.save_ordering(conn, ids_of_active_assets)
            return assets_helper.read(conn, asset['asset_id']), 201


class AssetV1_2(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'parameters': [
            {
                'name': 'asset_id',
                'type': 'string',
                'in': 'path',
                'description': 'id of an asset'
            }
        ],
        'responses': {
            '200': {
                'description': 'Asset',
                'schema': AssetModel
            }
        }
    })
    def get(self, asset_id):
        with db.conn(settings['database']) as conn:
            return assets_helper.read(conn, asset_id)

    @swagger.doc({
        'parameters': [
            {
                'name': 'asset_id',
                'type': 'string',
                'in': 'path',
                'description': 'ID of an asset',
                'required': True
            },
            {
                'in': 'body',
                'name': 'properties',
                'description': 'Properties of an asset',
                'schema': AssetPropertiesModel,
                'required': True
            }
        ],
        'responses': {
            '200': {
                'description': 'Asset updated',
                'schema': AssetModel
            }
        }
    })
    def patch(self, asset_id):
        data = json.loads(request.data)
        with db.conn(settings['database']) as conn:

            asset = assets_helper.read(conn, asset_id)
            if not asset:
                raise Exception('Asset not found.')
            update_asset(asset, data)

            assets = assets_helper.read(conn)
            ids_of_active_assets = [x['asset_id'] for x in assets if x['is_active']]

            asset = assets_helper.update(conn, asset_id, asset)

            try:
                ids_of_active_assets.remove(asset['asset_id'])
            except ValueError:
                pass
            if asset['is_active']:
                ids_of_active_assets.insert(asset['play_order'], asset['asset_id'])

            assets_helper.save_ordering(conn, ids_of_active_assets)
            return assets_helper.read(conn, asset_id)

    @swagger.doc({
        'parameters': [
            {
                'name': 'asset_id',
                'type': 'string',
                'in': 'path',
                'description': 'id of an asset',
                'required': True
            },
            {
                'in': 'body',
                'name': 'model',
                'description': 'Adds an asset',
                'schema': AssetRequestModel,
                'required': True
            }
        ],
        'responses': {
            '200': {
                'description': 'Asset updated',
                'schema': AssetModel
            }
        }
    })
    def put(self, asset_id):
        asset = prepare_asset_v1_2(request, asset_id)
        with db.conn(settings['database']) as conn:
            assets = assets_helper.read(conn)
            ids_of_active_assets = [x['asset_id'] for x in assets if x['is_active']]

            asset = assets_helper.update(conn, asset_id, asset)

            try:
                ids_of_active_assets.remove(asset['asset_id'])
            except ValueError:
                pass
            if asset['is_active']:
                ids_of_active_assets.insert(asset['play_order'], asset['asset_id'])

            assets_helper.save_ordering(conn, ids_of_active_assets)
            return assets_helper.read(conn, asset_id)

    @swagger.doc({
        'parameters': [
            {
                'name': 'asset_id',
                'type': 'string',
                'in': 'path',
                'description': 'id of an asset',
                'required': True

            },
        ],
        'responses': {
            '204': {
                'description': 'Deleted'
            }
        }
    })
    def delete(self, asset_id):
        with db.conn(settings['database']) as conn:
            asset = assets_helper.read(conn, asset_id)
            try:
                if asset['uri'].startswith(settings['assetdir']):
                    remove(asset['uri'])
            except OSError:
                pass
            assets_helper.delete(conn, asset_id)
            return '', 204  # return an OK with no content


class FileAsset(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'parameters': [
            {
                'name': 'file_upload',
                'type': 'file',
                'in': 'formData',
                'description': 'File to be sent'
            }
        ],
        'responses': {
            '200': {
                'description': 'File path',
                'schema': {
                    'type': 'string'
                }
            }
        }
    })
    def post(self):
        req = Request(request.environ)
        file_upload = req.files.get('file_upload')
        filename = file_upload.filename.encode('utf-8')
        file_type = guess_type(filename)[0]

        if not file_type:
            raise Exception("Invalid file type.")

        if file_type.split('/')[0] not in ['image', 'video']:
            raise Exception("Invalid file type.")

        file_path = path.join(settings['assetdir'], uuid.uuid5(uuid.NAMESPACE_URL, filename).hex) + ".tmp"

        if 'Content-Range' in request.headers:
            range_str = request.headers['Content-Range']
            start_bytes = int(range_str.split(' ')[1].split('-')[0])
            with open(file_path, 'a') as f:
                f.seek(start_bytes)
                f.write(file_upload.read())
        else:
            file_upload.save(file_path)

        return {'uri': file_path, 'ext': guess_extension(file_type)}


class PlaylistOrder(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'parameters': [
            {
                'name': 'ids',
                'in': 'formData',
                'type': 'string',
                'description':
                    '''
                    Content-Type: application/x-www-form-urlencoded
                    ids: "793406aa1fd34b85aa82614004c0e63a,1c5cfa719d1f4a9abae16c983a18903b,9c41068f3b7e452baf4dc3f9b7906595"
                    comma separated ids
                    '''
            },
        ],
        'responses': {
            '204': {
                'description': 'Sorted'
            }
        }
    })
    def post(self):
        with db.conn(settings['database']) as conn:
            assets_helper.save_ordering(conn, request.form.get('ids', '').split(','))


class Backup(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'responses': {
            '200': {
                'description': 'Backup filename',
                'schema': {
                    'type': 'string'
                }
            }
        }
    })
    def post(self):
        filename = backup_helper.create_backup(name=settings['player_name'])
        return filename, 201


class Recover(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'parameters': [
            {
                'name': 'backup_upload',
                'type': 'file',
                'in': 'formData'
            }
        ],
        'responses': {
            '200': {
                'description': 'Recovery successful'
            }
        }
    })
    def post(self):
        publisher = ZmqPublisher.get_instance()
        req = Request(request.environ)
        file_upload = (req.files['backup_upload'])
        filename = file_upload.filename

        if guess_type(filename)[0] != 'application/x-tar':
            raise Exception("Incorrect file extension.")
        try:
            publisher.send_to_viewer('stop')
            location = path.join("static", filename)
            file_upload.save(location)
            backup_helper.recover(location)
            return "Recovery successful."
        finally:
            publisher.send_to_viewer('play')


class ResetWifiConfig(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'responses': {
            '204': {
                'description': 'Deleted'
            }
        }
    })
    def get(self):
        home = getenv('HOME')
        file_path = path.join(home, '.screenly/initialized')

        if path.isfile(file_path):
            remove(file_path)

        bus = pydbus.SystemBus()

        pattern_include = re.compile("wlan*")
        pattern_exclude = re.compile("ScreenlyOSE-*")

        wireless_connections = get_active_connections(bus)

        if wireless_connections is not None:
            device_uuid = None

            wireless_connections = filter(
                lambda c: not pattern_exclude.search(str(c['Id'])),
                filter(
                    lambda c: pattern_include.search(str(c['Devices'])),
                    wireless_connections
                )
            )

            if len(wireless_connections) > 0:
                device_uuid = wireless_connections[0].get('Uuid')

            if not device_uuid:
                raise Exception('The device has no active connection.')

            remove_connection(bus, device_uuid)

        return '', 204


class GenerateUsbAssetsKey(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'responses': {
            '200': {
                'description': 'Usb assets key generated',
                'schema': {
                    'type': 'string'
                }
            }
        }
    })
    def get(self):
        settings['usb_assets_key'] = generate_perfect_paper_password(20, False)
        settings.save()

        return settings['usb_assets_key']


class UpgradeScreenly(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'responses': {
            '200': {
                'description': 'Upgrade system'
            }
        }
    })
    def post(self):
        for task in celery.control.inspect(timeout=2.0).active().get('worker@screenly'):
            if task.get('type') == 'server.upgrade_screenly':
                return jsonify({'id': task.get('id')})
        branch = request.form.get('branch')
        manage_network = request.form.get('manage_network')
        system_upgrade = request.form.get('system_upgrade')
        task = upgrade_screenly.apply_async(args=(branch, manage_network, system_upgrade))
        return jsonify({'id': task.id})


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


class RebootScreenly(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'responses': {
            '200': {
                'description': 'Reboot system'
            }
        }
    })
    def post(self):
        reboot_screenly.apply_async()
        return '', 200


class ShutdownScreenly(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'responses': {
            '200': {
                'description': 'Shutdown system'
            }
        }
    })
    def post(self):
        shutdown_screenly.apply_async()
        return '', 200


class Info(Resource):
    method_decorators = [api_response, authorized]

    def get(self):
        viewlog = "Not yet implemented"

        # Calculate disk space
        slash = statvfs("/")
        free_space = size(slash.f_bavail * slash.f_frsize)
        display_power = r.get('display_power')

        return {
            'viewlog': viewlog,
            'loadavg': diagnostics.get_load_avg()['15 min'],
            'free_space': free_space,
            'display_info': diagnostics.get_monitor_status(),
            'display_power': display_power,
            'up_to_date': is_up_to_date()
        }


class AssetsControl(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'parameters': [
            {
                'name': 'command',
                'type': 'string',
                'in': 'path',
                'description':
                    '''
                    Control commands:
                    next - show next asset
                    previous - show previous asset
                    asset&asset_id - show asset with `asset_id` id
                    '''
            }
        ],
        'responses': {
            '200': {
                'description': 'Asset switched'
            }
        }
    })
    def get(self, command):
        publisher = ZmqPublisher.get_instance()
        publisher.send_to_viewer(command)
        return "Asset switched"


class AssetContent(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'parameters': [
            {
                'name': 'asset_id',
                'type': 'string',
                'in': 'path',
                'description': 'id of an asset'
            }
        ],
        'responses': {
            '200': {
                'description':
                    '''
                    The content of the asset.

                    'type' can either be 'file' or 'url'.

                    In case of a file, the fields 'mimetype', 'filename', and 'content'  will be present.
                    In case of a URL, the field 'url' will be present.
                    ''',
                'schema': AssetContentModel
            }
        }
    })
    def get(self, asset_id):
        with db.conn(settings['database']) as conn:
            asset = assets_helper.read(conn, asset_id)

        if isinstance(asset, list):
            raise Exception('Invalid asset ID provided')

        if path.isfile(asset['uri']):
            filename = asset['name']

            with open(asset['uri'], 'rb') as f:
                content = f.read()

            mimetype = guess_type(filename)[0]
            if not mimetype:
                mimetype = 'application/octet-stream'

            result = {
                'type': 'file',
                'filename': filename,
                'content': b64encode(content),
                'mimetype': mimetype
            }
        else:
            result = {
                'type': 'url',
                'url': asset['uri']
            }

        return result


class ViewerCurrentAsset(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'responses': {
            '200': {
                'description': 'Currently displayed asset in viewer',
                'schema': AssetModel
            }
        }
    })
    def get(self):
        collector = ZmqCollector.get_instance()

        publisher = ZmqPublisher.get_instance()
        publisher.send_to_viewer('current_asset_id')

        collector_result = collector.recv_json(2000)
        current_asset_id = collector_result.get('current_asset_id')

        if not current_asset_id:
            return []

        with db.conn(settings['database']) as conn:
            return assets_helper.read(conn, current_asset_id)


api.add_resource(Assets, '/api/v1/assets')
api.add_resource(Asset, '/api/v1/assets/<asset_id>')
api.add_resource(AssetsV1_1, '/api/v1.1/assets')
api.add_resource(AssetV1_1, '/api/v1.1/assets/<asset_id>')
api.add_resource(AssetsV1_2, '/api/v1.2/assets')
api.add_resource(AssetV1_2, '/api/v1.2/assets/<asset_id>')
api.add_resource(AssetContent, '/api/v1/assets/<asset_id>/content')
api.add_resource(FileAsset, '/api/v1/file_asset')
api.add_resource(PlaylistOrder, '/api/v1/assets/order')
api.add_resource(Backup, '/api/v1/backup')
api.add_resource(Recover, '/api/v1/recover')
api.add_resource(AssetsControl, '/api/v1/assets/control/<command>')
api.add_resource(Info, '/api/v1/info')
api.add_resource(ResetWifiConfig, '/api/v1/reset_wifi')
api.add_resource(GenerateUsbAssetsKey, '/api/v1/generate_usb_assets_key')
api.add_resource(UpgradeScreenly, '/api/v1/upgrade_screenly')
api.add_resource(RebootScreenly, '/api/v1/reboot_screenly')
api.add_resource(ShutdownScreenly, '/api/v1/shutdown_screenly')
api.add_resource(ViewerCurrentAsset, '/api/v1/viewer_current_asset')

try:
    my_ip = get_node_ip()
except Exception:
    pass
else:
    SWAGGER_URL = '/api/docs'
    swagger_address = getenv("SWAGGER_HOST", my_ip)

    if settings['use_ssl'] or is_demo_node:
        API_URL = 'https://{}/api/swagger.json'.format(swagger_address)
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
# Views
################################


@app.route('/')
@authorized
def viewIndex():
    player_name = settings['player_name']
    my_ip = urlparse(request.host_url).hostname
    is_demo = is_demo_node()
    resin_uuid = getenv("RESIN_UUID", None)

    ws_addresses = []

    if settings['use_ssl']:
        ws_addresses.append('wss://' + my_ip + '/ws/')
    else:
        ws_addresses.append('ws://' + my_ip + '/ws/')

    if resin_uuid:
        ws_addresses.append('wss://{}.resindevice.io/ws/'.format(resin_uuid))

    return template('index.html', ws_addresses=ws_addresses, player_name=player_name, is_demo=is_demo)


@app.route('/settings', methods=["GET", "POST"])
@authorized
def settings_page():
    context = {'flash': None}

    if request.method == "POST":
        try:
            # put some request variables in local variables to make easier to read
            current_pass = request.form.get('current-password', '')
            auth_backend = request.form.get('auth_backend', '')

            if auth_backend != settings['auth_backend'] and settings['auth_backend']:
                if not current_pass:
                    raise ValueError("Must supply current password to change authentication method")
                if not settings.auth.check_password(current_pass):
                    raise ValueError("Incorrect current password.")

            prev_auth_backend = settings['auth_backend']
            if not current_pass and prev_auth_backend:
                current_pass_correct = None
            else:
                current_pass_correct = settings.auth_backends[prev_auth_backend].check_password(current_pass)
            next_auth_backend = settings.auth_backends[auth_backend]
            next_auth_backend.update_settings(current_pass_correct)
            settings['auth_backend'] = auth_backend

            for field, default in CONFIGURABLE_SETTINGS.items():
                value = request.form.get(field, default)

                if not value and field in ['default_duration', 'default_streaming_duration']:
                    value = str(0)
                if isinstance(default, bool):
                    value = value == 'on'

                if field == 'default_assets' and settings[field] != value:
                    if value:
                        add_default_assets()
                    else:
                        remove_default_assets()

                settings[field] = value

            settings.save()
            publisher = ZmqPublisher.get_instance()
            publisher.send_to_viewer('reload')
            context['flash'] = {'class': "success", 'message': "Settings were successfully saved."}
        except ValueError as e:
            context['flash'] = {'class': "danger", 'message': e}
        except IOError as e:
            context['flash'] = {'class': "danger", 'message': e}
        except OSError as e:
            context['flash'] = {'class': "danger", 'message': e}
    else:
        settings.load()
    for field, default in DEFAULTS['viewer'].items():
        if field == 'usb_assets_key':
            if not settings[field]:
                settings[field] = generate_perfect_paper_password(20, False)
                settings.save()
        context[field] = settings[field]

    auth_backends = []
    for backend in settings.auth_backends_list:
        if backend.template:
            html, ctx = backend.template
            context.update(ctx)
        else:
            html = None
        auth_backends.append({
            'name': backend.name,
            'text': backend.display_name,
            'template': html,
            'selected': 'selected' if settings['auth_backend'] == backend.name else ''
        })

    context.update({
        'user': settings['user'],
        'need_current_password': bool(settings['auth_backend']),
        'is_balena': is_balena_app(),
        'is_docker': is_docker(),
        'auth_backend': settings['auth_backend'],
        'auth_backends': auth_backends
    })

    return template('settings.html', **context)


@app.route('/system-info')
@authorized
def system_info():
    viewlog = ["Yet to be implemented"]

    loadavg = diagnostics.get_load_avg()['15 min']
    display_info = diagnostics.get_monitor_status()
    display_power = r.get('display_power')

    # Calculate disk space
    slash = statvfs("/")
    free_space = size(slash.f_bavail * slash.f_frsize)

    # Memory
    virtual_memory = psutil.virtual_memory()
    memory = "Total: {} | Used: {} | Free: {} | Shared: {} | Buff: {} | Available: {}".format(
        virtual_memory.total >> 20,
        virtual_memory.used >> 20,
        virtual_memory.free >> 20,
        virtual_memory.shared >> 20,
        virtual_memory.buffers >> 20,
        virtual_memory.available >> 20
    )

    # Get uptime
    system_uptime = timedelta(seconds=diagnostics.get_uptime())

    # Player name for title
    player_name = settings['player_name']

    raspberry_pi_revision = raspberry_pi_helper.parse_cpu_info().get('revision', False)
    if raspberry_pi_revision:
        raspberry_pi_details = raspberry_pi_helper.lookup_raspberry_pi_revision(
                raspberry_pi_revision
        )
        raspberry_pi_model = '{} ({})'.format(
            raspberry_pi_details['model'],
            raspberry_pi_details['manufacturer']
        )
    else:
        raspberry_pi_model = 'Unknown.'

    screenly_version = '{}@{}'.format(
        diagnostics.get_git_branch(),
        diagnostics.get_git_short_hash()
    )

    return template(
        'system-info.html',
        player_name=player_name,
        viewlog=viewlog,
        loadavg=loadavg,
        free_space=free_space,
        uptime=system_uptime,
        memory=memory,
        display_info=display_info,
        display_power=display_power,
        raspberry_pi_model=raspberry_pi_model,
        screenly_version=screenly_version,
        mac_address=get_node_mac_address()
    )


@app.route('/integrations')
@authorized
def integrations():

    context = {
        'player_name': settings['player_name'],
        'is_balena': is_balena_app(),
    }

    if context['is_balena']:
        context['balena_device_id'] = getenv('BALENA_DEVICE_UUID')
        context['balena_app_id'] = getenv('BALENA_APP_ID')
        context['balena_app_name'] = getenv('BALENA_APP_NAME')
        context['balena_supervisor_version'] = getenv('BALENA_SUPERVISOR_VERSION')
        context['balena_host_os_version'] = getenv('BALENA_HOST_OS_VERSION')
        context['balena_device_name_at_init'] = getenv('BALENA_DEVICE_NAME_AT_INIT')

    return template('integrations.html', **context)


@app.route('/splash-page')
def splash_page():
    my_ip = get_node_ip()
    return template('splash-page.html', my_ip=get_node_ip())


@app.errorhandler(403)
def mistake403(code):
    return 'The parameter you passed has the wrong format!'


@app.errorhandler(404)
def mistake404(code):
    return 'Sorry, this page does not exist!'


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


if __name__ == "__main__":
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
