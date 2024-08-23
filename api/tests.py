from django.test import TestCase
from django.urls import reverse
from inspect import cleandoc
from rest_framework.test import APIClient
from rest_framework import status


ASSET_LIST_V1_1_URL = reverse('api:asset_list_v1_1')
ASSET_MODEL_DATA = {
    'model': cleandoc(
        '''
        {
            "name": "Anthias",
            "uri": "https://anthias.screenly.io",
            "start_date": "2019-08-24T14:15:22Z",
            "end_date": "2029-08-24T14:15:22Z",
            "duration": "20",
            "mimetype": "webpage",
            "is_enabled": 0,
            "nocache": 0,
            "play_order": 0,
            "skip_asset_check": 0
        }
        '''
    )
}


class EndpointsTestV1_1(TestCase):
    def setUp(self):
        self.client = APIClient()

    def tearDown(self):
        pass

    def create_asset(self, data=ASSET_MODEL_DATA):
        return self.client.post(ASSET_LIST_V1_1_URL, data=data).data

    def update_asset(self, asset_id, data):
        return self.client.put(
            reverse('api:asset_detail_v1_1', args=[asset_id]),
            data=data
        ).data

    def get_asset(self, asset_id):
        url = reverse('api:asset_detail_v1_1', args=[asset_id])
        return self.client.get(url).data

    def delete_asset(self, asset_id):
        url = reverse('api:asset_detail_v1_1', args=[asset_id])
        return self.client.delete(url)

    def test_get_assets_when_first_time_setup_should_initially_return_empty(self):  # noqa: E501
        response = self.client.get(ASSET_LIST_V1_1_URL)
        assets = response.data

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(assets), 0)

    def test_create_asset_should_return_201(self):
        response = self.client.post(ASSET_LIST_V1_1_URL, data=ASSET_MODEL_DATA)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertEqual(response.data['name'], 'Anthias')
        self.assertEqual(response.data['uri'], 'https://anthias.screenly.io')
        self.assertEqual(response.data['is_enabled'], 0)
        self.assertEqual(response.data['nocache'], 0)
        self.assertEqual(response.data['play_order'], 0)
        self.assertEqual(response.data['skip_asset_check'], 0)

    def test_get_assets_after_create_should_return_1_asset(self):
        self.create_asset()

        response = self.client.get(ASSET_LIST_V1_1_URL)
        assets = response.data

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(assets), 1)

    def test_get_asset_by_id_should_return_asset(self):
        expected_asset = self.create_asset()
        asset_id = expected_asset['asset_id']

        actual_asset = self.get_asset(asset_id)

        self.assertEqual(expected_asset, actual_asset)

    def test_update_asset_should_return_updated_asset(self):
        expected_asset = self.create_asset()
        asset_id = expected_asset['asset_id']
        updated_asset = self.update_asset(
            asset_id,
            data={
                'model': cleandoc(
                    '''
                    {
                        "name": "Anthias",
                        "uri": "https://anthias.screenly.io",
                        "start_date": "2019-08-24T14:15:22Z",
                        "end_date": "2029-08-24T14:15:22Z",
                        "duration": "15",
                        "mimetype": "webpage",
                        "is_enabled": 1,
                        "nocache": 0,
                        "play_order": 0,
                        "skip_asset_check": 0
                    }
                    '''
                )
            }
        )

        self.assertEqual(updated_asset['name'], 'Anthias')
        self.assertEqual(updated_asset['uri'], 'https://anthias.screenly.io')
        self.assertEqual(updated_asset['duration'], '15')
        self.assertEqual(updated_asset['is_enabled'], 1)
        self.assertEqual(updated_asset['play_order'], 0)

    def test_delete_asset_should_return_204(self):
        asset = self.create_asset()
        asset_id = asset['asset_id']

        response = self.delete_asset(asset_id)
        assets = self.client.get(ASSET_LIST_V1_1_URL).data

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(len(assets), 0)
