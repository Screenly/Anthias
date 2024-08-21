import json
import uuid

from os import path, rename
from past.builtins import basestring
from dateutil import parser as date_parser
from lib import (
    assets_helper,
    db
)
from lib.utils import (
    download_video_from_youtube,
    get_video_duration,
    validate_url
)
from rest_framework import status
from rest_framework.views import exception_handler
from rest_framework.response import Response
from anthias_app.models import Asset
from settings import settings


def prepare_asset(request, unique_name=False):
    data = None

    # For backward compatibility
    try:
        data = json.loads(request.data)
    except ValueError:
        data = json.loads(request.data['model'])
    except TypeError:
        data = json.loads(request.data['model'])

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

    name = get('name')

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

    uri = get('uri')

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


def prepare_asset_v1_2(request, asset_id=None, unique_name=False):
    data = request.data

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
            "Not enough information provided. Please specify 'name', 'uri', "
            "'mimetype', 'is_enabled', 'start_date' and 'end_date'."
        )

    ampfix = "&amp;"
    # @TODO: Escape ampersands in the name.
    name = get('name').replace(ampfix, '&')
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
        path_name = path.join(settings['assetdir'], asset['asset_id'])
        ext_name = get('ext')
        new_uri = f'{path_name}{ext_name}'
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
            key in ['asset_id', 'is_processing', 'mimetype', 'uri']
            or key not in asset
        ):
            continue

        if key in ['start_date', 'end_date']:
            value = date_parser.parse(value).replace(tzinfo=None)

        if (
            key in [
                'play_order',
                'skip_asset_check',
                'is_enabled',
                'is_active',
                'nocache',
            ]
        ):
            value = int(value)

        if key == 'duration':
            if "video" not in asset['mimetype']:
                continue
            value = int(value)

        asset.update({key: value})


def custom_exception_handler(exc, context):
    exception_handler(exc, context)

    return Response(
        {'error': str(exc)},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR
    )


def get_active_asset_ids():
    enabled_assets = Asset.objects.filter(
        is_enabled=1,
        start_date__isnull=False,
        end_date__isnull=False,
    )
    return [
        asset.asset_id
        for asset in enabled_assets
        if asset.is_active()
    ]


def save_active_assets_ordering(active_asset_ids):
    for i, asset_id in enumerate(active_asset_ids):
        Asset.objects.filter(asset_id=asset_id).update(play_order=i)
