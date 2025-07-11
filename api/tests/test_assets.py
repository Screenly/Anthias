"""
Tests for asset-related API endpoints.
"""
import mock
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from unittest_parametrize import ParametrizedTestCase, parametrize

from api.tests.test_common import (
    ASSET_CREATION_DATA,
    ASSET_UPDATE_DATA_V1_2,
    ASSET_UPDATE_DATA_V2,
    get_request_data,
)

parametrize_version = parametrize(
    'version',
    [('v1',), ('v1_1',), ('v1_2',), ('v2',)],
)


class CRUDAssetEndpointsTest(TestCase, ParametrizedTestCase):
    def setUp(self):
        self.client = APIClient()

    def get_assets(self, version):
        asset_list_url = reverse(f'api:asset_list_{version}')
        response = self.client.get(asset_list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        return response.data

    def create_asset(self, data, version):
        asset_list_url = reverse(f'api:asset_list_{version}')
        return self.client.post(
            asset_list_url,
            data=get_request_data(data, version)
        ).data

    def update_asset(self, asset_id, data, version):
        return self.client.put(
            reverse(f'api:asset_detail_{version}', args=[asset_id]),
            data=get_request_data(data, version)
        ).data

    def get_asset(self, asset_id, version):
        url = reverse(f'api:asset_detail_{version}', args=[asset_id])
        return self.client.get(url).data

    def delete_asset(self, asset_id, version):
        url = reverse(f'api:asset_detail_{version}', args=[asset_id])
        return self.client.delete(url)

    @parametrize_version
    def test_get_assets_when_first_time_setup_should_initially_return_empty(self, version):  # noqa: E501
        asset_list_url = reverse(f'api:asset_list_{version}')
        response = self.client.get(asset_list_url)
        assets = response.data

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(assets), 0)

    @parametrize_version
    def test_create_asset_should_return_201(self, version):
        asset_list_url = reverse(f'api:asset_list_{version}')
        response = self.client.post(
            asset_list_url,
            data=get_request_data(ASSET_CREATION_DATA, version)
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertEqual(response.data['name'], 'Anthias')
        self.assertEqual(response.data['uri'], 'https://anthias.screenly.io')
        self.assertEqual(response.data['is_enabled'], 0)
        self.assertEqual(response.data['nocache'], 0)
        self.assertEqual(response.data['play_order'], 0)
        self.assertEqual(response.data['skip_asset_check'], 0)

    @mock.patch('api.serializers.mixins.rename')
    @mock.patch('api.serializers.mixins.validate_uri')
    def test_create_video_asset_v2_with_non_zero_duration_should_fail(
        self,
        mock_validate_uri,
        mock_rename
    ):
        """Test that v2 rejects video assets with non-zero duration."""
        mock_validate_uri.return_value = True
        asset_list_url = reverse('api:asset_list_v2')

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
            'skip_asset_check': False
        }

        response = self.client.post(
            asset_list_url,
            data=test_data,
            format='json'
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        self.assertIn(
            'Duration must be zero for video assets',
            str(response.data)
        )

        self.assertEqual(mock_rename.call_count, 1)
        self.assertEqual(mock_validate_uri.call_count, 1)

    @parametrize_version
    def test_get_assets_after_create_should_return_1_asset(self, version):
        self.create_asset(ASSET_CREATION_DATA, version)

        assets = self.get_assets(version)
        self.assertEqual(len(assets), 1)

    @parametrize_version
    def test_get_asset_by_id_should_return_asset(self, version):
        expected_asset = self.create_asset(ASSET_CREATION_DATA, version)
        asset_id = expected_asset['asset_id']
        actual_asset = self.get_asset(asset_id, version)

        self.assertEqual(expected_asset, actual_asset)

    @parametrize_version
    def test_update_asset_should_return_updated_asset(self, version):
        expected_asset = self.create_asset(ASSET_CREATION_DATA, version)
        asset_id = expected_asset['asset_id']

        if version == 'v2':
            data = ASSET_UPDATE_DATA_V2
        else:
            data = ASSET_UPDATE_DATA_V1_2

        updated_asset = self.update_asset(
            asset_id,
            data=data,
            version=version,
        )

        self.assertEqual(updated_asset['name'], 'Anthias')
        self.assertEqual(updated_asset['uri'], 'https://anthias.screenly.io')
        self.assertEqual(updated_asset['duration'], data['duration'])
        self.assertEqual(updated_asset['is_enabled'], data['is_enabled'])
        self.assertEqual(updated_asset['play_order'], data['play_order'])

    @parametrize_version
    def test_delete_asset_should_return_204(self, version):
        asset = self.create_asset(ASSET_CREATION_DATA, version)
        asset_id = asset['asset_id']

        response = self.delete_asset(asset_id, version)
        assets = self.client.get(reverse('api:asset_list_v1_1')).data

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(len(assets), 0)
