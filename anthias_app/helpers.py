import uuid
import yaml
from datetime import datetime
from flask import render_template
from os import getenv, path

from lib import assets_helper, db
from lib.github import is_up_to_date
from lib.utils import get_video_duration
from settings import settings


def template(template_name, **context):
    """
    This is a template response wrapper that shares the
    same function signature as Flask's render_template() method
    but also injects some global context."""

    # Add global contexts
    context['date_format'] = settings['date_format']
    context['default_duration'] = settings['default_duration']
    context['default_streaming_duration'] = (
        settings['default_streaming_duration'])
    context['template_settings'] = {
        'imports': ['from lib.utils import template_handle_unicode'],
        'default_filters': ['template_handle_unicode'],
    }
    context['up_to_date'] = is_up_to_date()
    context['use_24_hour_clock'] = settings['use_24_hour_clock']

    return render_template(template_name, context=context)


def prepare_default_asset(**kwargs):
    if kwargs['mimetype'] not in ['image', 'video', 'webpage']:
        return

    asset_id = 'default_{}'.format(uuid.uuid4().hex)
    duration = (
        int(get_video_duration(kwargs['uri']).total_seconds())
        if "video" == kwargs['mimetype']
        else kwargs['duration']
    )

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

    default_assets_yaml = path.join(
        getenv('HOME'), '.screenly/default_assets.yml')

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
