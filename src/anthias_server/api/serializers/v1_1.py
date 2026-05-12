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

    # Set to ``'video'`` by ``prepare_asset`` when the freshly persisted
    # row needs the video-normalisation pipeline to run before the
    # viewer can play it (e.g. an MPEG-1 sample that lands on a board
    # whose player only handles H.264 / HEVC). The view then dispatches
    # ``normalize_video_asset`` via the shared ``dispatch_pending_normalize``
    # helper. ``None`` means "no normalisation needed".
    #
    # Image normalisation is deliberately *not* wired here — v1 / v1.1
    # never converted HEIC / HEIF / TIFF on upload, and changing that
    # would shift response-time and asset-state semantics for legacy
    # clients that depend on synchronous availability. The video gap
    # is the GH #2870 regression; image normalisation stays a v1.2 / v2
    # opt-in.
    _pending_normalize: str | None = None

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

        # Record whether this row came from a local upload (the
        # /api/v1/file_asset companion endpoint dropped the file at a
        # local path before the create POST). ``is_local_upload``
        # gates the normalize dispatch below: a remote HTTP / RTSP
        # video URL should never go through the on-device transcode
        # pipeline, only the locally-staged ``.tmp`` uploads do.
        is_local_upload = False
        if not asset['asset_id']:
            asset['asset_id'] = uuid.uuid4().hex
            if uri.startswith('/'):
                rename(uri, path.join(settings['assetdir'], asset['asset_id']))
                uri = path.join(settings['assetdir'], asset['asset_id'])
                is_local_upload = True

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

        # Flag the freshly-uploaded local video for the normalisation
        # pipeline. Fixes GH #2870: pre-fix, v1 / v1.1 left the file
        # in whatever codec the operator uploaded (e.g. MPEG-1 on an
        # x86 board with passthrough_video_codecs={'h264','hevc'}),
        # and the viewer silently skipped it forever. Set
        # ``is_processing=1`` so the viewer drops the in-flight row
        # from rotation until ``normalize_video_asset`` finalises it.
        # Image normalisation deliberately stays opt-in via v1.2 / v2
        # — see the class-level ``_pending_normalize`` docstring.
        if (
            is_local_upload
            and not is_youtube
            and 'video' in (asset['mimetype'] or '')
        ):
            self._pending_normalize = 'video'
            asset['is_processing'] = 1

        return asset

    def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        return self.prepare_asset(data)
