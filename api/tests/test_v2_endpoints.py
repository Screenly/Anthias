"""
Tests for V2 API endpoints.
"""

import hashlib
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def device_settings_url():
    return reverse('api:device_settings_v2')


@pytest.mark.django_db
class TestDeviceSettingsViewV2:
    @patch('api.views.v2.settings')
    def test_get_device_settings(
        self, settings_mock, api_client, device_settings_url
    ):
        settings_mock.__getitem__.side_effect = lambda key: {
            'player_name': 'Test Player',
            'audio_output': 'hdmi',
            'default_duration': '15',
            'default_streaming_duration': '100',
            'date_format': 'YYYY-MM-DD',
            'auth_backend': '',
            'show_splash': True,
            'default_assets': [],
            'shuffle_playlist': False,
            'use_24_hour_clock': True,
            'debug_logging': False,
            'user': '',
        }[key]

        response = api_client.get(device_settings_url)
        assert response.status_code == status.HTTP_200_OK

        expected = {
            'player_name': 'Test Player',
            'audio_output': 'hdmi',
            'default_duration': 15,
            'default_streaming_duration': 100,
            'date_format': 'YYYY-MM-DD',
            'auth_backend': '',
            'show_splash': True,
            'default_assets': [],
            'shuffle_playlist': False,
            'use_24_hour_clock': True,
            'debug_logging': False,
            'username': '',
        }
        for key, expected_value in expected.items():
            assert response.data[key] == expected_value

    @patch('api.views.v2.settings')
    def test_patch_invalid_auth_backend(
        self, settings_mock, api_client, device_settings_url
    ):
        settings_mock.load = MagicMock()
        settings_mock.save = MagicMock()
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

        response = api_client.patch(
            device_settings_url,
            data={'auth_backend': 'invalid_auth'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'auth_backend' in response.data
        assert 'is not a valid choice' in str(response.data['auth_backend'])
        settings_mock.load.assert_not_called()
        settings_mock.save.assert_not_called()

    @patch('api.views.v2.settings')
    @patch('api.views.v2.send_to_viewer')
    def test_patch_success(
        self,
        send_to_viewer_mock,
        settings_mock,
        api_client,
        device_settings_url,
    ):
        settings_mock.load = MagicMock()
        settings_mock.save = MagicMock()
        settings_mock.__getitem__.side_effect = lambda key: {
            'player_name': 'Old Player',
            'audio_output': 'local',
            'default_duration': '10',
            'default_streaming_duration': '50',
            'date_format': 'DD-MM-YYYY',
            'auth_backend': '',
            'show_splash': False,
            'default_assets': [],
            'shuffle_playlist': True,
            'use_24_hour_clock': False,
            'debug_logging': True,
            'user': '',
        }[key]
        settings_mock.__setitem__ = MagicMock()

        data = {
            'player_name': 'New Player',
            'audio_output': 'hdmi',
            'default_duration': 20,
            'show_splash': True,
            'username': '',
        }
        response = api_client.patch(
            device_settings_url, data=data, format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['message'] == 'Settings were successfully saved.'
        settings_mock.load.assert_called_once()
        settings_mock.save.assert_called_once()
        assert settings_mock.__setitem__.call_count == 5
        send_to_viewer_mock.assert_called_once_with('reload')

    @patch('api.views.v2.settings')
    def test_patch_validation_error(
        self, settings_mock, api_client, device_settings_url
    ):
        data = {
            'default_duration': 'not_an_integer',
            'show_splash': 'not_a_boolean',
        }
        response = api_client.patch(
            device_settings_url, data=data, format='json'
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'default_duration' in response.data
        assert 'show_splash' in response.data
        settings_mock.load.assert_not_called()
        settings_mock.save.assert_not_called()

    @patch('api.views.v2.settings')
    @patch('api.views.v2.send_to_viewer')
    def test_enable_basic_auth(
        self,
        send_to_viewer_mock,
        settings_mock,
        api_client,
        device_settings_url,
    ):
        settings_mock.load = MagicMock()
        settings_mock.save = MagicMock()
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
        settings_mock.__setitem__ = MagicMock()

        auth_basic_mock = MagicMock()
        auth_basic_mock.name = 'auth_basic'
        auth_basic_mock.check_password.return_value = True
        auth_none_mock = MagicMock()
        auth_none_mock.name = ''
        auth_none_mock.check_password.return_value = True

        settings_mock.auth_backends = {
            'auth_basic': auth_basic_mock,
            '': auth_none_mock,
        }
        settings_mock.auth = auth_none_mock

        data = {
            'auth_backend': 'auth_basic',
            'username': 'testuser',
            'password': 'testpass',
            'password_2': 'testpass',
        }
        expected_hashed = hashlib.sha256(
            'testpass'.encode('utf-8')
        ).hexdigest()

        response = api_client.patch(
            device_settings_url, data=data, format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['message'] == 'Settings were successfully saved.'
        settings_mock.load.assert_called_once()
        settings_mock.save.assert_called_once()
        settings_mock.__setitem__.assert_any_call('auth_backend', 'auth_basic')
        settings_mock.__setitem__.assert_any_call('user', 'testuser')
        settings_mock.__setitem__.assert_any_call('password', expected_hashed)
        send_to_viewer_mock.assert_called_once_with('reload')

    @patch('api.views.v2.settings')
    @patch('api.views.v2.send_to_viewer')
    def test_disable_basic_auth(
        self,
        send_to_viewer_mock,
        settings_mock,
        api_client,
        device_settings_url,
    ):
        settings_mock.load = MagicMock()
        settings_mock.save = MagicMock()
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
        settings_mock.__setitem__ = MagicMock()
        settings_mock.auth_backends = {
            'auth_basic': MagicMock(),
            '': MagicMock(),
        }
        settings_mock.auth = MagicMock()
        settings_mock.auth.check_password.return_value = True

        data = {
            'auth_backend': '',
            'current_password': 'testpass',
        }
        response = api_client.patch(
            device_settings_url, data=data, format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['message'] == 'Settings were successfully saved.'
        settings_mock.load.assert_called_once()
        settings_mock.save.assert_called_once()
        settings_mock.__setitem__.assert_any_call('auth_backend', '')
        send_to_viewer_mock.assert_called_once_with('reload')

        response = api_client.get(device_settings_url)
        assert response.status_code == status.HTTP_200_OK

    @patch('api.views.v2.settings')
    @patch('api.views.v2.send_to_viewer')
    @patch('api.views.v2.add_default_assets')
    @patch('api.views.v2.remove_default_assets')
    def test_patch_default_assets(
        self,
        remove_default_assets_mock,
        add_default_assets_mock,
        send_to_viewer_mock,
        settings_mock,
        api_client,
        device_settings_url,
    ):
        settings_mock.load = MagicMock()
        settings_mock.save = MagicMock()
        settings_mock.__getitem__.side_effect = lambda key: {
            'player_name': 'Test Player',
            'auth_backend': '',
            'audio_output': 'local',
            'default_duration': '10',
            'default_streaming_duration': '50',
            'date_format': 'DD-MM-YYYY',
            'show_splash': False,
            'default_assets': False,
            'shuffle_playlist': True,
            'use_24_hour_clock': False,
            'debug_logging': True,
        }[key]
        settings_mock.__setitem__ = MagicMock()

        # Enable default assets
        response = api_client.patch(
            device_settings_url,
            data={'default_assets': True},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['message'] == 'Settings were successfully saved.'
        settings_mock.load.assert_called_once()
        settings_mock.save.assert_called_once()
        settings_mock.__setitem__.assert_any_call('default_assets', True)
        add_default_assets_mock.assert_called_once()
        remove_default_assets_mock.assert_not_called()
        send_to_viewer_mock.assert_called_once_with('reload')

        # Reset mocks
        settings_mock.load.reset_mock()
        settings_mock.save.reset_mock()
        settings_mock.__setitem__.reset_mock()
        add_default_assets_mock.reset_mock()
        remove_default_assets_mock.reset_mock()
        send_to_viewer_mock.reset_mock()

        # Simulate default assets enabled
        settings_mock.__getitem__.side_effect = lambda key: {
            'player_name': 'Test Player',
            'auth_backend': '',
            'audio_output': 'local',
            'default_duration': '10',
            'default_streaming_duration': '50',
            'date_format': 'DD-MM-YYYY',
            'show_splash': False,
            'default_assets': True,
            'shuffle_playlist': True,
            'use_24_hour_clock': False,
            'debug_logging': True,
        }[key]

        # Disable default assets
        response = api_client.patch(
            device_settings_url,
            data={'default_assets': False},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['message'] == 'Settings were successfully saved.'
        settings_mock.load.assert_called_once()
        settings_mock.save.assert_called_once()
        settings_mock.__setitem__.assert_any_call('default_assets', False)
        remove_default_assets_mock.assert_called_once()
        add_default_assets_mock.assert_not_called()
        send_to_viewer_mock.assert_called_once_with('reload')


@pytest.mark.django_db
class TestIntegrationsViewV2:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = APIClient()
        self.integrations_url = reverse('api:integrations_v2')

    @patch('api.views.v2.is_balena_app')
    @patch('api.views.v2.getenv')
    def test_balena_environment(self, mock_getenv, mock_is_balena):
        mock_is_balena.side_effect = lambda: True
        mock_getenv.side_effect = lambda x: {
            'BALENA_DEVICE_UUID': 'test-device-uuid',
            'BALENA_APP_ID': 'test-app-id',
            'BALENA_APP_NAME': 'test-app-name',
            'BALENA_SUPERVISOR_VERSION': ('test-supervisor-version'),
            'BALENA_HOST_OS_VERSION': ('test-host-os-version'),
            'BALENA_DEVICE_NAME_AT_INIT': 'test-device-name',
        }.get(x)

        response = self.client.get(self.integrations_url)
        assert response.status_code == 200
        assert response.json() == {
            'is_balena': True,
            'balena_device_id': 'test-device-uuid',
            'balena_app_id': 'test-app-id',
            'balena_app_name': 'test-app-name',
            'balena_supervisor_version': ('test-supervisor-version'),
            'balena_host_os_version': ('test-host-os-version'),
            'balena_device_name_at_init': 'test-device-name',
        }

    @patch('api.views.v2.is_balena_app')
    def test_non_balena_environment(self, mock_is_balena):
        mock_is_balena.side_effect = lambda: False

        response = self.client.get(self.integrations_url)
        assert response.status_code == 200
        assert response.json() == {
            'is_balena': False,
            'balena_device_id': None,
            'balena_app_id': None,
            'balena_app_name': None,
            'balena_supervisor_version': None,
            'balena_host_os_version': None,
            'balena_device_name_at_init': None,
        }
