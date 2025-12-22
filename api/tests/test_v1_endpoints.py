"""
Tests for V1 API endpoints.
"""

import os
from pathlib import Path
from unittest import mock

from django.conf import settings as django_settings
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from unittest_parametrize import ParametrizedTestCase, parametrize

from anthias_app.models import Asset
from api.tests.test_common import ASSET_CREATION_DATA
from settings import settings as anthias_settings


class V1EndpointsTest(TestCase, ParametrizedTestCase):
    def setUp(self):
        self.client = APIClient()

    def tearDown(self):
        self.remove_all_asset_files()

    def remove_all_asset_files(self):
        asset_directory_path = Path(anthias_settings['assetdir'])
        for file in asset_directory_path.iterdir():
            file.unlink()

    def get_asset_content_url(self, asset_id):
        return reverse('api:asset_content_v1', args=[asset_id])

    def test_asset_content(self):
        asset = Asset.objects.create(**ASSET_CREATION_DATA)
        asset_id = asset.asset_id

        response = self.client.get(self.get_asset_content_url(asset_id))
        data = response.data

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(data['type'], 'url')
        self.assertEqual(data['url'], 'https://anthias.screenly.io')

    def test_file_asset(self):
        project_base_path = django_settings.BASE_DIR
        image_path = os.path.join(
            project_base_path,
            'static/img/standby.png',
        )

        response = self.client.post(
            reverse('api:file_asset_v1'),
            data={
                'file_upload': open(image_path, 'rb'),
            },
        )
        data = response.data

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(os.path.exists(data['uri']))
        self.assertEqual(data['ext'], '.png')

    def test_playlist_order(self):
        playlist_order_url = reverse('api:playlist_order_v1')

        for asset_name in ['Asset #1', 'Asset #2', 'Asset #3']:
            Asset.objects.create(
                **{
                    **ASSET_CREATION_DATA,
                    'name': asset_name,
                }
            )

        self.assertTrue(
            all([asset.play_order == 0 for asset in Asset.objects.all()])
        )

        asset_1, asset_2, asset_3 = Asset.objects.all()
        asset_ids = [asset_1.asset_id, asset_2.asset_id, asset_3.asset_id]

        response = self.client.post(
            playlist_order_url, data={'ids': ','.join(asset_ids)}
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        for asset in [asset_1, asset_2, asset_3]:
            asset.refresh_from_db()

        self.assertEqual(asset_1.play_order, 0)
        self.assertEqual(asset_2.play_order, 1)
        self.assertEqual(asset_3.play_order, 2)

    @parametrize(
        'command',
        [
            ('next',),
            ('previous',),
            ('asset&6ee2394e760643748b9353f06f405424',),
        ],
    )
    @mock.patch('api.views.v1.ZmqPublisher.send_to_viewer', return_value=None)
    def test_assets_control(self, send_to_viewer_mock, command):
        assets_control_url = reverse('api:assets_control_v1', args=[command])
        response = self.client.get(assets_control_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(send_to_viewer_mock.call_count, 1)
        self.assertEqual(send_to_viewer_mock.call_args[0][0], command)
        self.assertEqual(response.data, 'Asset switched')

    @mock.patch(
        'api.views.mixins.reboot_anthias.apply_async',
        side_effect=(lambda: None),
    )
    def test_reboot(self, reboot_anthias_mock):
        reboot_url = reverse('api:reboot_v1')
        response = self.client.post(reboot_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(reboot_anthias_mock.call_count, 1)

    @mock.patch(
        'api.views.mixins.shutdown_anthias.apply_async',
        side_effect=(lambda: None),
    )
    def test_shutdown(self, shutdown_anthias_mock):
        shutdown_url = reverse('api:shutdown_v1')
        response = self.client.post(shutdown_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(shutdown_anthias_mock.call_count, 1)

    @mock.patch('api.views.v1.ZmqPublisher.send_to_viewer', return_value=None)
    def test_viewer_current_asset(self, send_to_viewer_mock):
        asset = Asset.objects.create(
            **{
                **ASSET_CREATION_DATA,
                'is_enabled': 1,
            }
        )
        asset_id = asset.asset_id

        with mock.patch(
            'api.views.v1.ZmqCollector.recv_json',
            side_effect=(lambda _: {'current_asset_id': asset_id}),
        ):
            viewer_current_asset_url = reverse('api:viewer_current_asset_v1')
            response = self.client.get(viewer_current_asset_url)
            data = response.data

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(send_to_viewer_mock.call_count, 1)

            self.assertEqual(data['asset_id'], asset_id)
            self.assertEqual(data['is_active'], 1)
