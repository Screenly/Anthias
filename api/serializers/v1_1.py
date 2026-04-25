import uuid
from datetime import timezone
from os import path, rename
from typing import Any

from rest_framework.serializers import (
    BooleanField,
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


class CreateAssetSerializerV1_1(Serializer[dict[str, Any]]):
    def __init__(
        self,
        *args: Any,
        unique_name: bool = False,
        **kwargs: Any,
    ) -> None:
        self.unique_name = unique_name
        super().__init__(*args, **kwargs)

    name = CharField()
    uri = CharField()
    start_date = DateTimeField(default_timezone=timezone.utc, required=False)
    end_date = DateTimeField(default_timezone=timezone.utc, required=False)
    duration = CharField(required=False)
    mimetype = CharField()
    is_enabled = IntegerField(min_value=0, max_value=1, required=False)
    is_processing = IntegerField(min_value=0, max_value=1, required=False)
    nocache = BooleanField(required=False)
    play_order = IntegerField(required=False)
    skip_asset_check = IntegerField(min_value=0, max_value=1, required=False)

    def prepare_asset(self, data: dict[str, Any]) -> dict[str, Any]:
        name = data['name']

        if self.unique_name:
            name = get_unique_name(name)

        asset = {
            'name': name,
            'mimetype': data.get('mimetype'),
            'asset_id': data.get('asset_id'),
            'is_enabled': data.get('is_enabled', 0),
            'is_processing': data.get('is_processing', 0),
            'nocache': data.get('nocache', 0),
        }

        uri: str = data['uri']

        validate_uri(uri)

        if not asset['asset_id']:
            asset['asset_id'] = uuid.uuid4().hex
            if uri.startswith('/'):
                rename(uri, path.join(settings['assetdir'], asset['asset_id']))
                uri = path.join(settings['assetdir'], asset['asset_id'])

        if 'youtube_asset' in asset['mimetype']:
            (uri, asset['name'], asset['duration']) = (
                download_video_from_youtube(uri, asset['asset_id'])
            )
            asset['mimetype'] = 'video'
            asset['is_processing'] = 1

        asset['uri'] = uri

        if 'video' in asset['mimetype']:
            duration_raw = data.get('duration')
            if duration_raw is not None and int(duration_raw) == 0:
                video_duration = get_video_duration(uri)
                assert video_duration is not None
                asset['duration'] = int(video_duration.total_seconds())
        else:
            # Crashes if it's not an int. We want that.
            asset['duration'] = int(data['duration'])

        asset['skip_asset_check'] = int(data.get('skip_asset_check', 0))

        start_date = data.get('start_date')
        if start_date:
            asset['start_date'] = start_date.replace(tzinfo=None)
        else:
            asset['start_date'] = ''

        end_date = data.get('end_date')
        if end_date:
            asset['end_date'] = end_date.replace(tzinfo=None)
        else:
            asset['end_date'] = ''

        if not asset['skip_asset_check'] and url_fails(asset['uri']):
            raise Exception('Could not retrieve file. Check the asset URL.')

        return asset

    def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        return self.prepare_asset(data)
