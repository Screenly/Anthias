import uuid
from inspect import cleandoc
from os import path, rename
from typing import Any

from rest_framework.serializers import CharField, Serializer

from anthias_server.api.errors import AssetCreationError
from anthias_common.utils import (
    get_video_duration,
    url_fails,
)
from anthias_common.youtube import youtube_destination_path
from anthias_server.processing import needs_image_normalisation
from anthias_server.settings import settings

from . import (
    get_unique_name,
    validate_uri,
)


class CreateAssetSerializerMixin:
    unique_name: bool = False
    # Source URL of an in-flight YouTube asset, set by prepare_asset
    # when ``mimetype == 'youtube_asset'``. The view reads this after
    # Asset.objects.create() to dispatch download_youtube_asset.delay
    # with the freshly persisted row's id. Stashing it here (instead
    # of inside the asset dict) keeps Asset.objects.create from
    # choking on an unknown column. None means "not a YouTube asset"
    # and the view skips the dispatch.
    _pending_youtube_uri: str | None = None

    # Set to ``'image'`` or ``'video'`` by ``prepare_asset`` when the
    # newly created row needs the normalisation pipeline to run before
    # the viewer can play it. The view dispatches the matching Celery
    # task once the row is persisted, mirroring the existing
    # ``_pending_youtube_uri`` hand-off. ``None`` means "no
    # normalisation needed" — most uploads (JPEG/PNG/WebP/MP4 H.264)
    # land in this path.
    _pending_normalize: str | None = None

    def prepare_asset(
        self,
        data: dict[str, Any],
        asset_id: str | None = None,
        version: str = 'v2',
    ) -> dict[str, Any]:
        ampersand_fix = '&amp;'
        name = data['name'].replace(ampersand_fix, '&')

        if self.unique_name:
            name = get_unique_name(name)

        asset = {
            'name': name,
            'mimetype': data.get('mimetype'),
            'is_enabled': data.get(
                'is_enabled', False if version == 'v2' else 0
            ),
            'nocache': data.get('nocache', False if version == 'v2' else 0),
        }

        uri = (
            data['uri']
            .replace(ampersand_fix, '&')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace("'", '&apos;')
            .replace('"', '&quot;')
        )

        validate_uri(uri)

        if not asset_id:
            asset['asset_id'] = uuid.uuid4().hex

        is_local_upload = False
        if not asset_id and uri.startswith('/'):
            path_name = path.join(settings['assetdir'], asset['asset_id'])
            ext_name = data.get('ext', '')
            new_uri = f'{path_name}{ext_name}'
            rename(uri, new_uri)
            uri = new_uri
            is_local_upload = True

        # Exact match — substring `'youtube_asset' in mimetype`
        # would also fire on `not_youtube_asset` and crash on a
        # missing/None mimetype. Both bugs were inherited from the
        # pre-celery code path.
        is_youtube = asset['mimetype'] == 'youtube_asset'
        if is_youtube:
            # Defer the download to download_youtube_asset (Celery).
            # The row lands with mimetype='video', is_processing set,
            # uri at the eventual local path, and a placeholder
            # duration that the task overwrites with the real value.
            # The viewer treats local files whose path doesn't exist
            # yet as not-displayable, so the in-flight row is silently
            # skipped during rotation.
            asset['mimetype'] = 'video'
            asset['is_processing'] = True if version == 'v2' else 1
            asset['duration'] = 0
            self._pending_youtube_uri = uri
            uri = youtube_destination_path(asset['asset_id'], settings)

        asset['uri'] = uri

        # Decide whether the new row needs the normalisation pipeline.
        # Only locally-uploaded files are eligible: YouTube downloads
        # are already handled by their own Celery task (and land at
        # the asset's eventual ``.mp4`` URI), and HTTP / RTSP URIs
        # never get rewritten in-place. Anything that goes through the
        # pipeline lands as ``is_processing=True`` so the viewer skips
        # it during rotation until the task clears the flag.
        needs_image = is_local_upload and needs_image_normalisation(uri)
        needs_video = (
            is_local_upload
            and not is_youtube
            and 'video' in (asset['mimetype'] or '')
        )

        if 'video' in asset['mimetype'] and not is_youtube:
            duration_raw = data.get('duration')
            if duration_raw is not None and int(duration_raw) == 0:
                if needs_video:
                    # Defer ffprobe to ``normalize_video_asset``: a
                    # passthrough-eligible upload still needs a
                    # duration probe, but doing it inline here would
                    # double the work for transcoded files (the
                    # task re-runs probe on the .mp4 it produces).
                    # 0 is the row's placeholder until the task
                    # finalises it — same convention used by
                    # ``download_youtube_asset``.
                    asset['duration'] = 0
                else:
                    video_duration = get_video_duration(uri)
                    if video_duration is None:
                        raise AssetCreationError(
                            f'Could not determine duration of video {uri!r}'
                        )
                    duration = video_duration.total_seconds()
                    asset['duration'] = (
                        duration if version == 'v2' else int(duration)
                    )
            else:
                raise AssetCreationError(
                    'Duration must be zero for video assets.'
                )
        elif not is_youtube:
            # Crashes if it's not an int. We want that.
            duration = data.get('duration', settings['default_duration'])

            if version == 'v2':
                asset['duration'] = duration
            else:
                asset['duration'] = int(duration)

        asset['play_order'] = (
            data.get('play_order') if data.get('play_order') else 0
        )

        skip_check_raw = data.get('skip_asset_check')
        asset['skip_asset_check'] = (
            int(skip_check_raw)
            if skip_check_raw is not None and int(skip_check_raw)
            else 0
        )

        start_date = data['start_date']
        end_date = data['end_date']
        asset['start_date'] = start_date.replace(tzinfo=None)
        asset['end_date'] = end_date.replace(tzinfo=None)

        for field in ('play_days', 'play_time_from', 'play_time_to'):
            if field in data:
                asset[field] = data[field]

        # Skip the reachability probe for in-flight YouTube rows: the
        # local mp4 path is the *future* destination, the file does
        # not exist yet, and url_fails on a schemeless path is a
        # silent no-op anyway. Asserting that explicitly prevents a
        # future url_fails change from breaking the create flow.
        if (
            not is_youtube
            and not asset['skip_asset_check']
            and url_fails(asset['uri'])
        ):
            raise AssetCreationError(
                'Could not retrieve file. Check the asset URL.'
            )

        # Hand the row off to the normalisation pipeline. We do this
        # *after* the reachability probe so a typo'd URL still 400s
        # synchronously instead of getting stuck in "Processing" until
        # the task fails. ``_pending_normalize`` tells the create view
        # which Celery task to dispatch with the freshly persisted
        # asset_id; ``is_processing`` blocks the viewer from picking
        # up the row mid-conversion.
        if needs_image:
            self._pending_normalize = 'image'
            asset['is_processing'] = True if version == 'v2' else 1
        elif needs_video:
            self._pending_normalize = 'video'
            asset['is_processing'] = True if version == 'v2' else 1

        return asset


class PlaylistOrderSerializerMixin(Serializer[Any]):
    ids = CharField(
        write_only=True,
        help_text=cleandoc(
            """
            Comma-separated list of asset IDs in the order
            they should be played. For example:

            `793406aa1fd34b85aa82614004c0e63a,1c5cfa719d1f4a9abae16c983a18903b,9c41068f3b7e452baf4dc3f9b7906595`
            """
        ),
    )


class BackupViewSerializerMixin(Serializer[Any]):
    pass


class RebootViewSerializerMixin(Serializer[Any]):
    pass


class ShutdownViewSerializerMixin(Serializer[Any]):
    pass
