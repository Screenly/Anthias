import json
from typing import Any

from dateutil import parser as date_parser
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from anthias_common.remote_video import dispatch_remote_video_download
from anthias_common.youtube import dispatch_download
from anthias_server.app.models import Asset
from anthias_server.processing import dispatch_pending_normalize
from anthias_server.settings import ViewerPublisher


class AssetCreationError(Exception):
    def __init__(self, errors: Any) -> None:
        self.errors = errors


def update_asset(asset: dict[str, Any], data: dict[str, Any]) -> None:
    for key, value in list(data.items()):
        if (
            key in ['asset_id', 'is_processing', 'mimetype', 'uri']
            or key not in asset
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
            if 'video' not in asset['mimetype']:
                continue
            value = int(value)

        asset.update({key: value})


def custom_exception_handler(
    exc: Exception, context: dict[str, Any]
) -> Response:
    response = exception_handler(exc, context)
    if response is not None:
        # Use DRF's default response (correct 4xx status, structured body)
        # for known exception types like ValidationError / NotFound / etc.
        return response

    return Response(
        {'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
    )


def persist_new_asset(serializer: Any) -> Asset:
    """Persist a validated Create*Serializer into an ``Asset`` and fire
    the deferred pipelines it flagged, then splice the row into the
    active play ordering.

    Extracted from ``AssetListViewV2.post`` so the v2 create endpoint and
    the content importer (``lib.integrations``) create assets through one
    code path: the YouTube / remote-video download hand-off, the
    image/video normalise dispatch, the post-create ``metadata`` apply
    and the active-ordering insert must stay identical for both, or an
    imported asset would behave subtly differently from an uploaded one.

    Expects an already-validated serializer (``is_valid()`` called) that
    mixes in ``CreateAssetSerializerMixin`` — the ``_pending_*`` hand-off
    attributes are read here.
    """
    active_asset_ids = get_active_asset_ids()
    asset = Asset.objects.create(**serializer.data)

    # Apply ``metadata`` (set by the create serializer's validate() when
    # the operator passes ``refresh_interval_s``). It lives in
    # ``validated_data`` rather than ``serializer.data`` because
    # ``metadata`` isn't a declared field on the create serializer —
    # surfacing it as one would open the upload-pipeline-owned bag
    # (original_ext, transcoded, error_message) for arbitrary writes.
    post_create_metadata = serializer.validated_data.get('metadata')
    if post_create_metadata:
        existing = dict(asset.metadata or {})
        existing.update(post_create_metadata)
        asset.metadata = existing
        asset.save(update_fields=['metadata'])
    asset.refresh_from_db()

    # Kick off any out-of-band work the serializer flagged. The row is
    # already persisted with is_processing=True where relevant; the task
    # fills in the file/duration and clears the flag.
    if serializer._pending_youtube_uri:
        dispatch_download(asset.asset_id, serializer._pending_youtube_uri)
    if serializer._pending_remote_video_uri:
        dispatch_remote_video_download(
            asset.asset_id, serializer._pending_remote_video_uri
        )
    dispatch_pending_normalize(serializer, asset.asset_id)

    if asset.is_active():
        active_asset_ids.insert(asset.play_order, asset.asset_id)
    save_active_assets_ordering(active_asset_ids)
    asset.refresh_from_db()
    return asset


def get_active_asset_ids() -> list[str]:
    enabled_assets = Asset.objects.filter(
        is_enabled=True,
        start_date__isnull=False,
        end_date__isnull=False,
    )
    return [asset.asset_id for asset in enabled_assets if asset.is_active()]


def save_active_assets_ordering(active_asset_ids: list[str]) -> None:
    for i, asset_id in enumerate(active_asset_ids):
        Asset.objects.filter(asset_id=asset_id).update(play_order=i)


def finalize_asset_update(asset: Asset) -> None:
    """Post-save housekeeping shared by v1_2/v2 ``AssetView.update``.

    Reorders the active-asset list around the just-saved row's new
    activeness (an edit can flip is_enabled, push the row out of its
    date range, or trip its play_days / play_time window) and wakes
    the viewer so it can skip past the asset if it's still on screen
    but no longer active (issue #2430).
    """
    active_asset_ids = get_active_asset_ids()
    asset.refresh_from_db()

    try:
        active_asset_ids.remove(asset.asset_id)
    except ValueError:
        pass

    if asset.is_active():
        active_asset_ids.insert(asset.play_order, asset.asset_id)

    save_active_assets_ordering(active_asset_ids)
    asset.refresh_from_db()

    ViewerPublisher.get_instance().send_to_viewer('reload')


def parse_request(request: Any) -> Any:
    data = None

    # For backward compatibility
    try:
        data = json.loads(request.data)
    except ValueError:
        data = json.loads(request.data['model'])
    except TypeError:
        data = json.loads(request.data['model'])

    return data
