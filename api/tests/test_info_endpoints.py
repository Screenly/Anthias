"""
Tests for Info API endpoints (v2).
"""

from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


@pytest.mark.django_db
class TestInfoEndpoints:
    @patch('api.views.v2.getenv', return_value='testuser')
    @patch(
        'api.views.v2.get_node_ip',
        return_value='192.168.1.100 10.0.0.50',
    )
    @patch(
        'api.views.v2.get_node_mac_address',
        return_value='00:11:22:33:44:55',
    )
    @patch(
        'api.views.v2.psutil.virtual_memory',
        return_value=MagicMock(
            total=8192 << 20,
            used=4096 << 20,
            free=4096 << 20,
            shared=0,
            buffers=1024 << 20,
            available=7168 << 20,
        ),
    )
    @patch(
        'api.views.v2.diagnostics.get_uptime',
        return_value=86400,
    )
    @patch(
        'api.views.v2.device_helper.parse_cpu_info',
        return_value={'model': 'Raspberry Pi 4'},
    )
    @patch(
        'api.views.v2.diagnostics.get_git_short_hash',
        return_value='a1b2c3d',
    )
    @patch(
        'api.views.v2.diagnostics.get_git_branch',
        return_value='main',
    )
    @patch(
        'api.views.v2.get_display_power_value',
        return_value='on',
    )
    @patch('api.views.v2.statvfs', MagicMock())
    @patch('api.views.v2.size', return_value='20G')
    @patch(
        'lib.diagnostics.get_load_avg',
        return_value={'15 min': 0.25},
    )
    @patch('api.views.v2.is_up_to_date', return_value=True)
    def test_info_v2_endpoint(self, *mocks):
        (
            is_up_to_date_mock,
            get_load_avg_mock,
            size_mock,
            display_power_mock,
            get_git_branch_mock,
            get_git_short_hash_mock,
            parse_cpu_info_mock,
            get_uptime_mock,
            virtual_memory_mock,
            mac_address_mock,
            get_node_ip_mock,
            getenv_mock,
        ) = mocks

        client = APIClient()
        info_url = reverse('api:info_v2')
        response = client.get(info_url)
        data = response.data

        assert response.status_code == status.HTTP_200_OK

        for mock_obj in [
            display_power_mock,
            size_mock,
            get_load_avg_mock,
            is_up_to_date_mock,
            get_git_branch_mock,
            get_git_short_hash_mock,
            parse_cpu_info_mock,
            get_uptime_mock,
            virtual_memory_mock,
            mac_address_mock,
            get_node_ip_mock,
            getenv_mock,
        ]:
            assert mock_obj.call_count == 1

        expected = {
            'viewlog': 'Not yet implemented',
            'loadavg': 0.25,
            'free_space': '20G',
            'display_power': 'on',
            'up_to_date': True,
            'anthias_version': 'main@a1b2c3d',
            'device_model': 'Raspberry Pi 4',
            'uptime': {'days': 1, 'hours': 0.0},
            'memory': {
                'total': 8192,
                'used': 4096,
                'free': 4096,
                'shared': 0,
                'buff': 1024,
                'available': 7168,
            },
            'ip_addresses': [
                'http://192.168.1.100',
                'http://10.0.0.50',
            ],
            'mac_address': '00:11:22:33:44:55',
            'host_user': 'testuser',
        }
        for key, expected_value in expected.items():
            assert data[key] == expected_value
