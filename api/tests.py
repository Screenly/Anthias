from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient


class EndpointsTestV1_1(TestCase):
    def setUp(self):
        pass

    def tearDown(self):
        pass

    # @TODO: Fix database migrations to make this test pass.
    def test_get_asset_should_return_empty(self):
        client = APIClient()
        response = client.get(reverse('api:asset_list_v1_1'))

        self.assertEqual(response.status_code, 200)
