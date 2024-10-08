import json
import traceback
import uuid

from dateutil import parser as date_parser
from flask import escape, make_response
from functools import wraps
from os import path, rename
from past.builtins import basestring
from flask_restful_swagger_2 import Schema
from werkzeug.wrappers import Request

from lib import assets_helper, db
from lib.utils import (
    download_video_from_youtube,
    json_dump,
    get_video_duration,
    validate_url,
)
from settings import settings


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
    required = [
        'name', 'uri', 'mimetype', 'is_enabled', 'start_date', 'end_date']


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


def api_error(error):
    return make_response(json_dump({'error': error}), 500)


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
        if isinstance(val, str):
            return val.strip()
        elif isinstance(val, basestring):
            return val.strip().decode('utf-8')
        else:
            return val

    if not all([get('name'), get('uri'), get('mimetype')]):
        raise Exception(
            "Not enough information provided. "
            "Please specify 'name', 'uri', and 'mimetype'."
        )

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

    uri = escape(get('uri'))

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
        uri, asset['name'], asset['duration'] = download_video_from_youtube(
            uri, asset['asset_id'])
        asset['mimetype'] = 'video'
        asset['is_processing'] = 1

    asset['uri'] = uri

    if "video" in asset['mimetype']:
        if get('duration') == 'N/A' or int(get('duration')) == 0:
            asset['duration'] = int(get_video_duration(uri).total_seconds())
    else:
        # Crashes if it's not an int. We want that.
        asset['duration'] = int(get('duration'))

    asset['skip_asset_check'] = (
        int(get('skip_asset_check'))
        if int(get('skip_asset_check'))
        else 0
    )

    # parse date via python-dateutil and remove timezone info
    if get('start_date'):
        asset['start_date'] = date_parser.parse(
            get('start_date')).replace(tzinfo=None)
    else:
        asset['start_date'] = ""

    if get('end_date'):
        asset['end_date'] = date_parser.parse(
            get('end_date')).replace(tzinfo=None)
    else:
        asset['end_date'] = ""

    return asset


def prepare_asset_v1_2(request_environ, asset_id=None, unique_name=False):
    data = json.loads(request_environ.data)

    def get(key):
        val = data.get(key, '')
        if isinstance(val, str):
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
            "Not enough information provided. Please specify 'name', "
            "'uri', 'mimetype', 'is_enabled', 'start_date' and 'end_date'."
        )

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

    uri = (
        (get('uri'))
        .replace(ampfix, '&')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('\'', '&apos;')
        .replace('\"', '&quot;')
    )

    if uri.startswith('/'):
        if not path.isfile(uri):
            raise Exception("Invalid file path. Failed to add asset.")
    else:
        if not validate_url(uri):
            raise Exception("Invalid URL. Failed to add asset.")

    if not asset_id:
        asset['asset_id'] = uuid.uuid4().hex

    if not asset_id and uri.startswith('/'):
        new_uri = "{}{}".format(
            path.join(settings['assetdir'], asset['asset_id']), get('ext'))
        rename(uri, new_uri)
        uri = new_uri

    if 'youtube_asset' in asset['mimetype']:
        uri, asset['name'], asset['duration'] = download_video_from_youtube(
            uri, asset['asset_id'])
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

    asset['skip_asset_check'] = (
        int(get('skip_asset_check'))
        if int(get('skip_asset_check'))
        else 0
    )

    # parse date via python-dateutil and remove timezone info
    asset['start_date'] = date_parser.parse(
        get('start_date')).replace(tzinfo=None)
    asset['end_date'] = date_parser.parse(get('end_date')).replace(tzinfo=None)

    return asset


def update_asset(asset, data):
    for key, value in list(data.items()):

        if (
            key in ['asset_id', 'is_processing', 'mimetype', 'uri'] or
            key not in asset
        ):
            continue

        if key in ['start_date', 'end_date']:
            value = date_parser.parse(value).replace(tzinfo=None)

        if key in [
            'play_order',
            'skip_asset_check',
            'is_enabled',
            'is_active',
            'nocache',
        ]:
            value = int(value)

        if key == 'duration':
            if "video" not in asset['mimetype']:
                continue
            value = int(value)

        asset.update({key: value})


# Used as a decorator to catch exceptions and return a JSON response.
def api_response(view):
    @wraps(view)
    def api_view(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except Exception as e:
            traceback.print_exc()
            return api_error(str(e))

    return api_view
