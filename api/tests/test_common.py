"""
Common test utilities and constants for the Anthias API tests.
"""

from django.urls import reverse

ASSET_LIST_V2_URL = reverse('api:asset_list_v2')
ASSET_CREATION_DATA = {
    'name': 'Anthias',
    'uri': 'https://anthias.screenly.io',
    'start_date': '2019-08-24T14:15:22Z',
    'end_date': '2029-08-24T14:15:22Z',
    'duration': 20,
    'mimetype': 'webpage',
    'is_enabled': True,
    'nocache': False,
    'play_order': 0,
    'skip_asset_check': False,
}
ASSET_UPDATE_DATA = {
    'name': 'Anthias',
    'uri': 'https://anthias.screenly.io',
    'start_date': '2019-08-24T14:15:22Z',
    'end_date': '2029-08-24T14:15:22Z',
    'duration': 15,
    'mimetype': 'webpage',
    'is_enabled': True,
    'nocache': False,
    'play_order': 0,
    'skip_asset_check': False,
}
