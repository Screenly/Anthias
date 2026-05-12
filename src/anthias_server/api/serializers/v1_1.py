import uuid
from datetime import timezone
from os import path, rename
from typing import Any

from rest_framework.exceptions import ValidationError
from rest_framework.serializers import (
    BooleanField,
    CharField,
    DateTimeField,
    IntegerField,
    Serializer,
)

from anthias_common.utils import (
    get_video_duration,
    url_fails,
)
from anthias_common.youtube import youtube_destination_path
from anthias_server.settings import settings

from . import (
    get_unique_name,
    validate_uri,
)


class CreateAssetSerializerV1_1(Serializer[dict[str, Any]]):
    # Source URL of an in-flight YouTube asset, set by prepare_asset
    # when ``mimetype == 'youtube_asset'``. The view dispatches the
    # download_youtube_asset task with this value after the row is
    # persisted. None means "not a YouTube asset" → skip dispatch.
    _pending_youtube_uri: str | None = None

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

        # Exact match — substring `'youtube_asset' in mimetype`
        # would also fire on `not_youtube_asset` and crash on a
        # missing/None mimetype. Both bugs predate the celery refactor.
        is_youtube = asset['mimetype'] == 'youtube_asset'
        if is_youtube:
            # Defer the download to download_youtube_asset (Celery).
            # The row is persisted with mimetype='video',
            # is_processing=1, uri at the eventual local path, and
            # duration=0 placeholder. The task overwrites name +
            # duration once yt-dlp completes.
            asset['mimetype'] = 'video'
            asset['is_processing'] = 1
            asset['duration'] = 0
            self._pending_youtube_uri = uri
            uri = youtube_destination_path(asset['asset_id'], settings)

        asset['uri'] = uri

        if 'video' in asset['mimetype'] and not is_youtube:
            duration_raw = data.get('duration')
            try:
                duration_int = (
                    None if duration_raw is None else int(duration_raw)
                )
            except (TypeError, ValueError):
                raise ValidationError(
                    {'duration': 'A valid integer is required.'}
                )

            # `duration_int is None` (omitted) and `0` both mean
            # "infer the duration from the file"; any other value is
            # taken as an explicit override the caller wants persisted.
            if duration_int is None or duration_int == 0:
                video_duration = get_video_duration(uri)
                if video_duration is None:
                    raise ValidationError(
                        {
                            'duration': (
                                'Could not determine video duration; '
                                'provide an explicit value.'
                            )
                        }
                    )
                asset['duration'] = int(video_duration.total_seconds())
            else:
                asset['duration'] = duration_int
        elif not is_youtube:
            duration_raw = data.get('duration')
            if duration_raw is None:
                raise ValidationError(
                    {
                        'duration': 'This field is required for non-video assets.'
                    }
                )
            try:
                asset['duration'] = int(duration_raw)
            except (TypeError, ValueError):
                raise ValidationError(
                    {'duration': 'A valid integer is required.'}
                )

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

        # Skip url_fails for in-flight YouTube assets — the local
        # mp4 destination doesn't exist until the Celery task lands.
        if (
            not is_youtube
            and not asset['skip_asset_check']
            and url_fails(asset['uri'])
        ):
            raise Exception('Could not retrieve file. Check the asset URL.')

        return asset

    def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        return self.prepare_asset(data)
