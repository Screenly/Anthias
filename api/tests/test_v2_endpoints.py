"""
Tests for V2 API endpoints.
"""
from unittest import mock
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


class DeviceSettingsViewV2Test(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.device_settings_url = reverse('api:device_settings_v2')

    @mock.patch('api.views.v2.settings')
    def test_get_device_settings(self, settings_mock):
        # Mock the settings values
        settings_mock.__getitem__.side_effect = lambda key: {
            'player_name': 'Test Player',
            'audio_output': 'hdmi',
            'default_duration': '15',
            'default_streaming_duration': '100',
            'date_format': 'YYYY-MM-DD',
            'auth_backend': 'none',
            'show_splash': True,
            'default_assets': [],
            'shuffle_playlist': False,
            'use_24_hour_clock': True,
            'debug_logging': False,
        }[key]

        response = self.client.get(self.device_settings_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_values = {
            'player_name': 'Test Player',
            'audio_output': 'hdmi',
            'default_duration': 15,
            'default_streaming_duration': 100,
            'date_format': 'YYYY-MM-DD',
            'auth_backend': 'none',
            'show_splash': True,
            'default_assets': [],
            'shuffle_playlist': False,
            'use_24_hour_clock': True,
            'debug_logging': False
        }

        for key, expected_value in expected_values.items():
            self.assertEqual(response.data[key], expected_value)


class TestIntegrationsViewV2(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.integrations_url = reverse('api:integrations_v2')

    @patch('api.views.v2.is_balena_app')
    @patch('api.views.v2.getenv')
    def test_integrations_balena_environment(
        self,
        mock_getenv,
        mock_is_balena
    ):
        # Mock Balena environment
        mock_is_balena.side_effect = lambda: True
        mock_getenv.side_effect = lambda x: {
            'BALENA_DEVICE_UUID': 'test-device-uuid',
            'BALENA_APP_ID': 'test-app-id',
            'BALENA_APP_NAME': 'test-app-name',
            'BALENA_SUPERVISOR_VERSION': 'test-supervisor-version',
            'BALENA_HOST_OS_VERSION': 'test-host-os-version',
            'BALENA_DEVICE_NAME_AT_INIT': 'test-device-name',
        }.get(x)

        response = self.client.get(self.integrations_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'is_balena': True,
            'balena_device_id': 'test-device-uuid',
            'balena_app_id': 'test-app-id',
            'balena_app_name': 'test-app-name',
            'balena_supervisor_version': 'test-supervisor-version',
            'balena_host_os_version': 'test-host-os-version',
            'balena_device_name_at_init': 'test-device-name',
        })

    @patch('api.views.v2.is_balena_app')
    def test_integrations_non_balena_environment(self, mock_is_balena):
        # Mock non-Balena environment
        mock_is_balena.side_effect = lambda: False

        response = self.client.get(self.integrations_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'is_balena': False,
        })
