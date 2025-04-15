"""
Common test utilities and constants for the Anthias API tests.
"""
import json

from django.urls import reverse

ASSET_LIST_V1_1_URL = reverse('api:asset_list_v1_1')
ASSET_CREATION_DATA = {
    'name': 'Anthias',
    'uri': 'https://anthias.screenly.io',
    'start_date': '2019-08-24T14:15:22Z',
    'end_date': '2029-08-24T14:15:22Z',
    'duration': 20,
    'mimetype': 'webpage',
    'is_enabled': 0,
    'nocache': 0,
    'play_order': 0,
    'skip_asset_check': 0
}
ASSET_UPDATE_DATA_V1_2 = {
    'name': 'Anthias',
    'uri': 'https://anthias.screenly.io',
    'start_date': '2019-08-24T14:15:22Z',
    'end_date': '2029-08-24T14:15:22Z',
    'duration': '15',
    'mimetype': 'webpage',
    'is_enabled': 1,
    'nocache': 0,
    'play_order': 0,
    'skip_asset_check': 0
}
ASSET_UPDATE_DATA_V2 = {
    **ASSET_UPDATE_DATA_V1_2,
    'duration': 15,
    'is_enabled': True,
    'nocache': False,
    'skip_asset_check': False,
}

def get_request_data(data, version):
    """Helper function to format request data based on API version."""
    if version in ['v1', 'v1_1']:
        return {
            'model': json.dumps(data)
        }
    else:
        return data
