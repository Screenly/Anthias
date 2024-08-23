from django.test import TestCase
from django.urls import reverse
from inspect import cleandoc
from rest_framework.test import APIClient
from rest_framework import status


ASSET_LIST_V1_1_URL = reverse('api:asset_list_v1_1')


class EndpointsTestV1_1(TestCase):
    def setUp(self):
        self.client = APIClient()

    def tearDown(self):
        pass

    def test_get_asset_when_first_time_setup_should_initially_return_empty(self):  # noqa: E501
        response = self.client.get(ASSET_LIST_V1_1_URL)
        assets = response.data

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(assets), 0)

    def test_create_asset_should_return_201(self):
        request_data = {
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
        response = self.client.post(ASSET_LIST_V1_1_URL, data=request_data)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertEqual(response.data['name'], 'Anthias')
        self.assertEqual(response.data['uri'], 'https://anthias.screenly.io')
        self.assertEqual(response.data['is_enabled'], 0)
        self.assertEqual(response.data['nocache'], 0)
        self.assertEqual(response.data['play_order'], 0)
        self.assertEqual(response.data['skip_asset_check'], 0)
