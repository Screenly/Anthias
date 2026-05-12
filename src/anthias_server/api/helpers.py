import json
from typing import Any

from dateutil import parser as date_parser
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from anthias_server.app.models import Asset
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
