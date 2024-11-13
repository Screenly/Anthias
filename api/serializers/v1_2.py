import uuid
from os import path, rename
from django.utils import timezone
from rest_framework.serializers import (
    CharField,
    DateTimeField,
    IntegerField,
    Serializer,
)

from lib.utils import (
    download_video_from_youtube,
    get_video_duration,
    url_fails,
)
from settings import settings
from . import (
    get_unique_name,
    validate_uri,
)


class CreateAssetSerializerV1_2(Serializer):
    def __init__(self, *args, unique_name=False, **kwargs):
        self.unique_name = unique_name
        super().__init__(*args, **kwargs)

    asset_id = CharField(read_only=True)
    ext = CharField(write_only=True, required=False)
    name = CharField()
    uri = CharField()
    start_date = DateTimeField(default_timezone=timezone.utc)
    end_date = DateTimeField(default_timezone=timezone.utc)
    duration = CharField()
    mimetype = CharField()
    is_enabled = IntegerField(min_value=0, max_value=1)
    is_processing = IntegerField(min_value=0, max_value=1, required=False)
    nocache = IntegerField(min_value=0, max_value=1, required=False)
    play_order = IntegerField(required=False)
    skip_asset_check = IntegerField(min_value=0, max_value=1, required=False)

    def prepare_asset(self, data, asset_id=None):
        ampersand_fix = '&amp;'
        name = data['name'].replace(ampersand_fix, '&')

        if self.unique_name:
            name = get_unique_name(name)

        asset = {
            'name': name,
            'mimetype': data.get('mimetype'),
            'is_enabled': data.get('is_enabled', 0),
            'nocache': data.get('nocache', 0),
        }

        uri = (
            data['uri']
            .replace(ampersand_fix, '&')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('\'', '&apos;')
            .replace('\"', '&quot;')
        )

        validate_uri(uri)

        if not asset_id:
            asset['asset_id'] = uuid.uuid4().hex

        if not asset_id and uri.startswith('/'):
            path_name = path.join(settings['assetdir'], asset['asset_id'])
            ext_name = data.get('ext', '')
            new_uri = f'{path_name}{ext_name}'
            rename(uri, new_uri)
            uri = new_uri

        if 'youtube_asset' in asset['mimetype']:
            (
                uri, asset['name'], asset['duration']
            ) = download_video_from_youtube(uri, asset['asset_id'])
            asset['mimetype'] = 'video'
            asset['is_processing'] = 1

        asset['uri'] = uri

        if "video" in asset['mimetype']:
            if int(data.get('duration')) == 0:
                asset['duration'] = int(
                    get_video_duration(uri).total_seconds())
        elif data.get('duration'):
            # Crashes if it's not an int. We want that.
            asset['duration'] = int(data.get('duration'))
        else:
            asset['duration'] = 10

        asset['play_order'] = (
            data.get('play_order') if data.get('play_order') else 0
        )

        asset['skip_asset_check'] = (
            int(data.get('skip_asset_check'))
            if int(data.get('skip_asset_check'))
            else 0
        )

        asset['start_date'] = data.get('start_date').replace(tzinfo=None)
        asset['end_date'] = data.get('end_date').replace(tzinfo=None)

        if not asset['skip_asset_check'] and url_fails(asset['uri']):
            raise Exception("Could not retrieve file. Check the asset URL.")

        return asset

    def validate(self, data):
        return self.prepare_asset(data)
