import json

from dateutil import parser as date_parser
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from anthias_app.models import Asset


class AssetCreationError(Exception):
    def __init__(self, errors):
        self.errors = errors


def update_asset(asset, data):
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


def custom_exception_handler(exc, context):
    exception_handler(exc, context)

    return Response(
        {'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
    )


def get_active_asset_ids():
    enabled_assets = Asset.objects.filter(
        is_enabled=1,
        start_date__isnull=False,
        end_date__isnull=False,
    )
    return [asset.asset_id for asset in enabled_assets if asset.is_active()]


def save_active_assets_ordering(active_asset_ids):
    for i, asset_id in enumerate(active_asset_ids):
        Asset.objects.filter(asset_id=asset_id).update(play_order=i)


def parse_request(request):
    data = None

    # For backward compatibility
    try:
        data = json.loads(request.data)
    except ValueError:
        data = json.loads(request.data['model'])
    except TypeError:
        data = json.loads(request.data['model'])

    return data
