"""
Tests for Info API endpoints (v1 and v2).
"""
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


class InfoEndpointsTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.info_url_v1 = reverse('api:info_v1')
        self.info_url_v2 = reverse('api:info_v2')

    def _assert_mock_calls(self, mocks):
        """Assert that all mocks were called exactly once."""
        for mock_obj in mocks:
            self.assertEqual(mock_obj.call_count, 1)

    def _assert_response_data(self, data, expected_data):
        """Assert that the response data matches the expected data."""
        for key, expected_value in expected_data.items():
            self.assertEqual(data[key], expected_value)

    @mock.patch(
        'api.views.mixins.is_up_to_date',
        return_value=False
    )
    @mock.patch(
        'lib.diagnostics.get_load_avg',
        return_value={'15 min': 0.11}
    )
    @mock.patch('api.views.mixins.size', return_value='15G')
    @mock.patch('api.views.mixins.statvfs', mock.MagicMock())
    @mock.patch('api.views.mixins.r.get', return_value='off')
    def test_info_v1_endpoint(
        self,
        redis_get_mock,
        size_mock,
        get_load_avg_mock,
        is_up_to_date_mock
    ):
        response = self.client.get(self.info_url_v1)
        data = response.data

        # Assert response status
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Assert mock calls
        self._assert_mock_calls([
            redis_get_mock,
            size_mock,
            get_load_avg_mock,
            is_up_to_date_mock
        ])

        # Assert response data
        expected_data = {
            'viewlog': 'Not yet implemented',
            'loadavg': 0.11,
            'free_space': '15G',
            'display_power': 'off',
            'up_to_date': False
        }
        self._assert_response_data(data, expected_data)

    @mock.patch(
        'api.views.v2.is_up_to_date',
        return_value=True
    )
    @mock.patch(
        'lib.diagnostics.get_load_avg',
        return_value={'15 min': 0.25}
    )
    @mock.patch('api.views.v2.size', return_value='20G')
    @mock.patch('api.views.v2.statvfs', mock.MagicMock())
    @mock.patch('api.views.v2.r.get', return_value='on')
    @mock.patch('api.views.v2.diagnostics.get_git_branch', return_value='main')
    @mock.patch(
        'api.views.v2.diagnostics.get_git_short_hash',
        return_value='a1b2c3d'
    )
    @mock.patch(
        'api.views.v2.device_helper.parse_cpu_info',
        return_value={'model': 'Raspberry Pi 4'}
    )
    @mock.patch(
        'api.views.v2.diagnostics.get_uptime',
        return_value=86400
    )
    @mock.patch(
        'api.views.v2.psutil.virtual_memory',
        return_value=mock.MagicMock(
            total=8192 << 20,  # 8GB
            used=4096 << 20,   # 4GB
            free=4096 << 20,   # 4GB
            shared=0,
            buffers=1024 << 20, # 1GB
            available=7168 << 20 # 7GB
        )
    )
    @mock.patch(
        'api.views.v2.get_node_mac_address',
        return_value='00:11:22:33:44:55'
    )
    def test_info_v2_endpoint(
        self,
        mac_address_mock,
        virtual_memory_mock,
        get_uptime_mock,
        parse_cpu_info_mock,
        get_git_short_hash_mock,
        get_git_branch_mock,
        redis_get_mock,
        size_mock,
        get_load_avg_mock,
        is_up_to_date_mock
    ):
        response = self.client.get(self.info_url_v2)
        data = response.data

        # Assert response status
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Assert mock calls
        self._assert_mock_calls([
            redis_get_mock,
            size_mock,
            get_load_avg_mock,
            is_up_to_date_mock,
            get_git_branch_mock,
            get_git_short_hash_mock,
            parse_cpu_info_mock,
            get_uptime_mock,
            virtual_memory_mock,
            mac_address_mock
        ])

        # Assert response data
        expected_data = {
            'viewlog': 'Not yet implemented',
            'loadavg': 0.25,
            'free_space': '20G',
            'display_power': 'on',
            'up_to_date': True,
            'anthias_version': 'main@a1b2c3d',
            'device_model': 'Raspberry Pi 4',
            'uptime': {
                'days': 1,
                'hours': 0.0
            },
            'memory': {
                'total': 8192,
                'used': 4096,
                'free': 4096,
                'shared': 0,
                'buff': 1024,
                'available': 7168
            },
            'mac_address': '00:11:22:33:44:55'
        }
        self._assert_response_data(data, expected_data)
