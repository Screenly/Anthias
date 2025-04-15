"""
Tests for V2 API endpoints.
"""
from unittest import mock

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
