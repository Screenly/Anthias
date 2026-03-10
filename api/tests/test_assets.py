"""
Tests for asset-related API endpoints (v2 only).
"""

from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from api.tests.test_common import (
    ASSET_CREATION_DATA,
    ASSET_LIST_V2_URL,
    ASSET_UPDATE_DATA,
)


@pytest.fixture
def api_client():
    return APIClient()


def _create_asset(api_client, data):
    return api_client.post(
        ASSET_LIST_V2_URL, data=data, format='json'
    ).data


@pytest.mark.django_db
class TestCRUDAssetEndpoints:
    def test_get_assets_initially_empty(self, api_client):
        response = api_client.get(ASSET_LIST_V2_URL)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

    def test_create_asset_returns_201(self, api_client):
        response = api_client.post(
            ASSET_LIST_V2_URL,
            data=ASSET_CREATION_DATA,
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['name'] == 'Anthias'
        assert response.data['uri'] == 'https://anthias.screenly.io'
        assert response.data['is_enabled'] is True
        assert response.data['nocache'] is False
        assert response.data['play_order'] == 0
        assert response.data['skip_asset_check'] is False

    @patch('api.serializers.mixins.rename')
    @patch('api.serializers.mixins.validate_uri')
    def test_create_video_asset_with_non_zero_duration_fails(
        self, mock_validate_uri, mock_rename, api_client
    ):
        mock_validate_uri.return_value = True
        test_data = {
            'name': 'Test Video',
            'uri': '/data/screenly_assets/video.mp4',
            'start_date': '2019-08-24T14:15:22Z',
            'end_date': '2029-08-24T14:15:22Z',
            'duration': 30,
            'mimetype': 'video',
            'is_enabled': True,
            'nocache': False,
            'play_order': 0,
            'skip_asset_check': False,
        }
        response = api_client.post(
            ASSET_LIST_V2_URL, data=test_data, format='json'
        )
        assert (
            response.status_code
            == status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        assert (
            'Duration must be zero for video assets'
            in str(response.data)
        )
        assert mock_rename.call_count == 1
        assert mock_validate_uri.call_count == 1

    def test_get_assets_after_create_returns_1(self, api_client):
        _create_asset(api_client, ASSET_CREATION_DATA)
        response = api_client.get(ASSET_LIST_V2_URL)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

    def test_get_asset_by_id(self, api_client):
        expected = _create_asset(api_client, ASSET_CREATION_DATA)
        asset_id = expected['asset_id']
        url = reverse('api:asset_detail_v2', args=[asset_id])
        actual = api_client.get(url).data
        assert expected == actual

    def test_update_asset(self, api_client):
        asset = _create_asset(api_client, ASSET_CREATION_DATA)
        asset_id = asset['asset_id']
        url = reverse('api:asset_detail_v2', args=[asset_id])
        updated = api_client.put(
            url, data=ASSET_UPDATE_DATA, format='json'
        ).data
        assert updated['name'] == 'Anthias'
        assert updated['uri'] == 'https://anthias.screenly.io'
        assert updated['duration'] == 15
        assert updated['is_enabled'] is True
        assert updated['play_order'] == 0

    def test_delete_asset_returns_204(self, api_client):
        asset = _create_asset(api_client, ASSET_CREATION_DATA)
        asset_id = asset['asset_id']
        url = reverse('api:asset_detail_v2', args=[asset_id])
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert len(api_client.get(ASSET_LIST_V2_URL).data) == 0
