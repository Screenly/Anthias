"""
Tests for V2 API endpoints.
"""
import hashlib
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
            'auth_backend': '',  # Updated to empty string as default
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
            'auth_backend': '',  # Updated to empty string as default
            'show_splash': True,
            'default_assets': [],
            'shuffle_playlist': False,
            'use_24_hour_clock': True,
            'debug_logging': False
        }

        for key, expected_value in expected_values.items():
            self.assertEqual(response.data[key], expected_value)

    @mock.patch('api.views.v2.settings')
    def test_patch_device_settings_invalid_auth_backend(self, settings_mock):
        # Mock settings methods
        settings_mock.load = mock.MagicMock()
        settings_mock.save = mock.MagicMock()
        settings_mock.__getitem__.side_effect = lambda key: {
            'player_name': 'Test Player',
            'auth_backend': '',
            'audio_output': 'local',
            'default_duration': '10',
            'default_streaming_duration': '50',
            'date_format': 'DD-MM-YYYY',
            'show_splash': False,
            'default_assets': [],
            'shuffle_playlist': True,
            'use_24_hour_clock': False,
            'debug_logging': True,
        }[key]

        # Test data with invalid auth_backend
        data = {
            'auth_backend': 'invalid_auth',  # Invalid value
        }

        response = self.client.patch(
            self.device_settings_url,
            data=data,
            format='json'
        )

        # Check response
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('auth_backend', response.data)
        self.assertIn(
            'is not a valid choice', str(response.data['auth_backend']))

        # Verify settings were not updated
        settings_mock.load.assert_not_called()
        settings_mock.save.assert_not_called()

    @mock.patch('api.views.v2.settings')
    @mock.patch('api.views.v2.ZmqPublisher')
    def test_patch_device_settings_success(
        self,
        publisher_mock,
        settings_mock
    ):
        # Mock settings methods
        settings_mock.load = mock.MagicMock()
        settings_mock.save = mock.MagicMock()
        settings_mock.__getitem__.side_effect = lambda key: {
            'player_name': 'Old Player',
            'audio_output': 'local',
            'default_duration': '10',
            'default_streaming_duration': '50',
            'date_format': 'DD-MM-YYYY',
            'auth_backend': '',  # Updated to empty string as default
            'show_splash': False,
            'default_assets': [],
            'shuffle_playlist': True,
            'use_24_hour_clock': False,
            'debug_logging': True,
        }[key]
        settings_mock.__setitem__ = mock.MagicMock()

        # Mock publisher
        publisher_instance = mock.MagicMock()
        publisher_mock.get_instance.return_value = publisher_instance

        # Test data
        data = {
            'player_name': 'New Player',
            'audio_output': 'hdmi',
            'default_duration': 20,
            'show_splash': True,
        }

        response = self.client.patch(
            self.device_settings_url,
            data=data,
            format='json'
        )

        # Check response
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['message'],
            'Settings were successfully saved.'
        )

        # Verify settings were updated
        settings_mock.load.assert_called_once()
        settings_mock.save.assert_called_once()
        self.assertEqual(
            settings_mock.__setitem__.call_count, 5
        )  # One for each field in data

        # Verify publisher was called
        publisher_instance.send_to_viewer.assert_called_once_with('reload')

    @mock.patch('api.views.v2.settings')
    def test_patch_device_settings_validation_error(self, settings_mock):
        # Test invalid data
        data = {
            'default_duration': 'not_an_integer',  # Should be an integer
            'show_splash': 'not_a_boolean',  # Should be a boolean
        }

        response = self.client.patch(
            self.device_settings_url,
            data=data,
            format='json'
        )

        # Check response
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('default_duration', response.data)
        self.assertIn('show_splash', response.data)

        # Verify settings were not updated
        settings_mock.load.assert_not_called()
        settings_mock.save.assert_not_called()

    @mock.patch('api.views.v2.settings')
    @mock.patch('api.views.v2.ZmqPublisher')
    def test_enable_basic_auth(self, publisher_mock, settings_mock):
        # Mock settings methods
        settings_mock.load = mock.MagicMock()
        settings_mock.save = mock.MagicMock()
        settings_mock.__getitem__.side_effect = lambda key: {
            'player_name': 'Test Player',
            'auth_backend': '',
            'user': '',
            'password': '',
            'audio_output': 'local',
            'default_duration': '10',
            'default_streaming_duration': '50',
            'date_format': 'DD-MM-YYYY',
            'show_splash': False,
            'default_assets': [],
            'shuffle_playlist': True,
            'use_24_hour_clock': False,
            'debug_logging': True,
        }[key]
        settings_mock.__setitem__ = mock.MagicMock()

        # Mock auth backends
        auth_basic_mock = mock.MagicMock()
        auth_basic_mock.name = 'auth_basic'
        auth_basic_mock.check_password.return_value = True

        auth_none_mock = mock.MagicMock()
        auth_none_mock.name = ''
        auth_none_mock.check_password.return_value = True

        settings_mock.auth_backends = {
            'auth_basic': auth_basic_mock,
            '': auth_none_mock,
        }
        settings_mock.auth = auth_none_mock

        # Mock publisher
        publisher_instance = mock.MagicMock()
        publisher_mock.get_instance.return_value = publisher_instance

        # Test data - enable basic auth
        data = {
            'auth_backend': 'auth_basic',
            'username': 'testuser',
            'password': 'testpass',
            'password_2': 'testpass',
        }

        # Calculate expected hashed password
        expected_hashed_password = hashlib.sha256(
            'testpass'.encode('utf-8')).hexdigest()

        response = self.client.patch(
            self.device_settings_url,
            data=data,
            format='json'
        )

        # Check response
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['message'],
            'Settings were successfully saved.'
        )

        # Verify settings were updated
        settings_mock.load.assert_called_once()
        settings_mock.save.assert_called_once()
        settings_mock.__setitem__.assert_any_call('auth_backend', 'auth_basic')
        settings_mock.__setitem__.assert_any_call('user', 'testuser')
        settings_mock.__setitem__.assert_any_call(
            'password', expected_hashed_password)

        # Verify publisher was called
        publisher_instance.send_to_viewer.assert_called_once_with('reload')

    @mock.patch('api.views.v2.settings')
    @mock.patch('api.views.v2.ZmqPublisher')
    def test_disable_basic_auth(self, publisher_mock, settings_mock):
        # Mock settings methods
        settings_mock.load = mock.MagicMock()
        settings_mock.save = mock.MagicMock()
        settings_mock.__getitem__.side_effect = lambda key: {
            'player_name': 'Test Player',
            'auth_backend': 'auth_basic',
            'user': 'testuser',
            'password': 'testpass',
            'audio_output': 'hdmi',
            'default_duration': '15',
            'default_streaming_duration': '100',
            'date_format': 'YYYY-MM-DD',
            'show_splash': True,
            'default_assets': [],
            'shuffle_playlist': False,
            'use_24_hour_clock': True,
            'debug_logging': False,
        }[key]
        settings_mock.__setitem__ = mock.MagicMock()
        settings_mock.auth_backends = {
            'auth_basic': mock.MagicMock(),
            '': mock.MagicMock(),
        }
        settings_mock.auth = mock.MagicMock()
        settings_mock.auth.check_password.return_value = True

        # Mock publisher
        publisher_instance = mock.MagicMock()
        publisher_mock.get_instance.return_value = publisher_instance

        # Test data - disable auth
        data = {
            'auth_backend': '',
            'current_password': 'testpass',
        }

        response = self.client.patch(
            self.device_settings_url,
            data=data,
            format='json'
        )

        # Check response
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['message'],
            'Settings were successfully saved.'
        )

        # Verify settings were updated
        settings_mock.load.assert_called_once()
        settings_mock.save.assert_called_once()
        settings_mock.__setitem__.assert_any_call('auth_backend', '')

        # Verify publisher was called
        publisher_instance.send_to_viewer.assert_called_once_with('reload')

        # Test that authentication is now disabled
        # Request should succeed without auth
        response = self.client.get(self.device_settings_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)


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
