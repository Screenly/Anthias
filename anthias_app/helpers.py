import uuid
from os import getenv, path

import yaml
from django.shortcuts import render
from django.utils import timezone

from anthias_app.models import Asset
from lib.github import is_up_to_date
from lib.utils import get_video_duration
from settings import settings


def template(request, template_name, context):
    """
    This is a helper function that is used to render a template
    with some global context. This is used to avoid having to
    repeat code in other views.
    """

    context['date_format'] = settings['date_format']
    context['default_duration'] = settings['default_duration']
    context['default_streaming_duration'] = (
        settings['default_streaming_duration']
    )
    context['template_settings'] = {
        'imports': ['from lib.utils import template_handle_unicode'],
        'default_filters': ['template_handle_unicode'],
    }
    context['up_to_date'] = is_up_to_date()
    context['use_24_hour_clock'] = settings['use_24_hour_clock']

    return render(request, template_name, context)


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

    datetime_now = timezone.now()
    default_asset_settings = {
        'start_date': datetime_now,
        'end_date': datetime_now.replace(year=datetime_now.year + 6),
        'duration': settings['default_duration']
    }

    default_assets_yaml = path.join(
        getenv('HOME'),
        '.screenly/default_assets.yml',
    )

    with open(default_assets_yaml, 'r') as yaml_file:
        default_assets = yaml.safe_load(yaml_file).get('assets')

        for default_asset in default_assets:
            default_asset_settings.update({
                'name': default_asset.get('name'),
                'uri': default_asset.get('uri'),
                'mimetype': default_asset.get('mimetype')
            })
            asset = prepare_default_asset(**default_asset_settings)

            if asset:
                Asset.objects.create(**asset)


def remove_default_assets():
    settings.load()

    for asset in Asset.objects.all():
        if asset.asset_id.startswith('default_'):
            asset.delete()
