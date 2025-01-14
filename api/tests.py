import json
from os import path
from pathlib import Path
from unittest import mock

from django.conf import settings as django_settings
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from unittest_parametrize import ParametrizedTestCase, parametrize

from anthias_app.models import Asset
from settings import settings as anthias_settings

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

    def get_request_data(self, data, version):
        if version in ['v1', 'v1_1']:
            return {
                'model': json.dumps(data)
            }
        else:
            return data

    def create_asset(self, data, version):
        asset_list_url = reverse(f'api:asset_list_{version}')
        return self.client.post(
            asset_list_url,
            data=self.get_request_data(data, version)
        ).data

    def update_asset(self, asset_id, data, version):
        return self.client.put(
            reverse(f'api:asset_detail_{version}', args=[asset_id]),
            data=self.get_request_data(data, version)
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
            data=self.get_request_data(ASSET_CREATION_DATA, version)
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertEqual(response.data['name'], 'Anthias')
        self.assertEqual(response.data['uri'], 'https://anthias.screenly.io')
        self.assertEqual(response.data['is_enabled'], 0)
        self.assertEqual(response.data['nocache'], 0)
        self.assertEqual(response.data['play_order'], 0)
        self.assertEqual(response.data['skip_asset_check'], 0)

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
        assets = self.client.get(ASSET_LIST_V1_1_URL).data

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(len(assets), 0)


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
        image_path = path.join(
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
        self.assertTrue(path.exists(data['uri']))
        self.assertEqual(data['ext'], '.png')

    def test_playlist_order(self):
        playlist_order_url = reverse('api:playlist_order_v1')

        for asset_name in ['Asset #1', 'Asset #2', 'Asset #3']:
            Asset.objects.create(**{
                **ASSET_CREATION_DATA,
                'name': asset_name,
            })

        self.assertTrue(
            all([
                asset.play_order == 0
                for asset in Asset.objects.all()
            ])
        )

        asset_1, asset_2, asset_3 = Asset.objects.all()
        asset_ids = [asset_1.asset_id, asset_2.asset_id, asset_3.asset_id]

        response = self.client.post(
            playlist_order_url,
            data={'ids': ','.join(asset_ids)}
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
        'api.views.v1.is_up_to_date',
        return_value=False
    )
    @mock.patch(
        'lib.diagnostics.get_load_avg',
        return_value={'15 min': 0.11}
    )
    @mock.patch('api.views.v1.size', return_value='15G')
    @mock.patch('api.views.v1.statvfs', mock.MagicMock())
    def test_device_info(
        self,
        size_mock,
        get_load_avg_mock,
        is_up_to_date_mock
    ):
        is_up_to_date_mock.return_value = False
        info_url = reverse('api:info_v1')
        response = self.client.get(info_url)
        data = response.data

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(size_mock.call_count, 1)
        self.assertEqual(get_load_avg_mock.call_count, 1)
        self.assertEqual(is_up_to_date_mock.call_count, 1)
        self.assertEqual(data['viewlog'], 'Not yet implemented')

    @mock.patch(
        'api.views.mixins.reboot_anthias.apply_async',
        side_effect=(lambda: None)
    )
    def test_reboot(self, reboot_anthias_mock):
        reboot_url = reverse('api:reboot_v1')
        response = self.client.post(reboot_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(reboot_anthias_mock.call_count, 1)

    @mock.patch(
        'api.views.mixins.shutdown_anthias.apply_async',
        side_effect=(lambda: None)
    )
    def test_shutdown(self, shutdown_anthias_mock):
        shutdown_url = reverse('api:shutdown_v1')
        response = self.client.post(shutdown_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(shutdown_anthias_mock.call_count, 1)

    @mock.patch('api.views.v1.ZmqPublisher.send_to_viewer', return_value=None)
    def test_viewer_current_asset(self, send_to_viewer_mock):
        asset = Asset.objects.create(**{
            **ASSET_CREATION_DATA,
            'is_enabled': 1,
        })
        asset_id = asset.asset_id

        with (
            mock.patch(
                'api.views.v1.ZmqCollector.recv_json',
                side_effect=(lambda _: {
                    'current_asset_id': asset_id
                })
            )
        ):
            viewer_current_asset_url = reverse('api:viewer_current_asset_v1')
            response = self.client.get(viewer_current_asset_url)
            data = response.data

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(send_to_viewer_mock.call_count, 1)

            self.assertEqual(data['asset_id'], asset_id)
            self.assertEqual(data['is_active'], 1)
