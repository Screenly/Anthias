"""
Tests for V2 API endpoints.
"""

from typing import Any
from unittest import mock
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

# Centralised fixture password for the basic-auth flow tests so Sonar's
# S2068 fires once per file, not once per test. Avoids dictionary
# words / breached-password tokens to keep S6437 quiet too. Never
# reaches a real credential store — only an in-memory test User row.
_FIXTURE_PASSWORD = 'fixture-v2-test-pwd'  # NOSONAR


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def device_settings_url() -> str:
    return reverse('api:device_settings_v2')


@pytest.mark.django_db
@mock.patch('anthias_server.api.views.v2.settings')
def test_get_device_settings(
    settings_mock: Any, api_client: APIClient, device_settings_url: str
) -> None:
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
        'prefer_dark_mode': True,
        'screen_rotation': 90,
    }[key]

    response = api_client.get(device_settings_url)

    assert response.status_code == status.HTTP_200_OK

    expected_values = {
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
        'prefer_dark_mode': True,
        'screen_rotation': 90,
        'username': '',
    }

    for key, expected_value in expected_values.items():
        assert response.data[key] == expected_value


@pytest.mark.django_db
@mock.patch('anthias_server.api.views.v2.settings')
def test_patch_device_settings_invalid_auth_backend(
    settings_mock: Any, api_client: APIClient, device_settings_url: str
) -> None:
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

    data = {
        'auth_backend': 'invalid_auth',
    }

    response = api_client.patch(device_settings_url, data=data, format='json')

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'auth_backend' in response.data
    assert 'is not a valid choice' in str(response.data['auth_backend'])

    settings_mock.load.assert_not_called()
    settings_mock.save.assert_not_called()


@pytest.mark.django_db
@mock.patch('anthias_server.api.views.v2.settings')
@mock.patch('anthias_server.api.views.v2.ViewerPublisher')
def test_patch_device_settings_success(
    publisher_mock: Any,
    settings_mock: Any,
    api_client: APIClient,
    device_settings_url: str,
) -> None:
    settings_mock.load = mock.MagicMock()
    settings_mock.save = mock.MagicMock()
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
    }[key]
    settings_mock.__setitem__ = mock.MagicMock()

    publisher_instance = mock.MagicMock()
    publisher_mock.get_instance.return_value = publisher_instance

    data = {
        'player_name': 'New Player',
        'audio_output': 'hdmi',
        'default_duration': 20,
        'show_splash': True,
        'username': '',
    }

    response = api_client.patch(device_settings_url, data=data, format='json')

    assert response.status_code == status.HTTP_200_OK
    assert response.data['message'] == 'Settings were successfully saved.'

    settings_mock.load.assert_called_once()
    settings_mock.save.assert_called_once()
    # auth_backend is always written by the patch flow (even when
    # unchanged); player_name, audio_output, default_duration,
    # show_splash come from the request → 5 writes total.
    assert settings_mock.__setitem__.call_count == 5

    publisher_instance.send_to_viewer.assert_called_once_with('reload')


@pytest.mark.django_db
@mock.patch('anthias_server.api.views.v2.settings')
def test_patch_device_settings_validation_error(
    settings_mock: Any, api_client: APIClient, device_settings_url: str
) -> None:
    data = {
        'default_duration': 'not_an_integer',
        'show_splash': 'not_a_boolean',
    }

    response = api_client.patch(device_settings_url, data=data, format='json')

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'default_duration' in response.data
    assert 'show_splash' in response.data

    settings_mock.load.assert_not_called()
    settings_mock.save.assert_not_called()


@pytest.mark.django_db
@mock.patch('anthias_server.api.views.v2.settings')
@mock.patch('anthias_server.api.views.v2.ViewerPublisher')
def test_enable_basic_auth(
    publisher_mock: Any,
    settings_mock: Any,
    api_client: APIClient,
    device_settings_url: str,
) -> None:
    """Auth disabled → enable: a User row gets created with the posted
    credentials, and settings['auth_backend'] flips to 'auth_basic'.
    Credentials live on the User now (not the conf), so we assert
    ``check_password`` round-trips instead of inspecting the conf."""
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
    settings_mock.__setitem__ = mock.MagicMock()

    publisher_instance = mock.MagicMock()
    publisher_mock.get_instance.return_value = publisher_instance

    data = {
        'auth_backend': 'auth_basic',
        'username': 'testuser',
        'password': _FIXTURE_PASSWORD,
        'password_2': _FIXTURE_PASSWORD,
    }

    response = api_client.patch(device_settings_url, data=data, format='json')

    assert response.status_code == status.HTTP_200_OK
    assert response.data['message'] == 'Settings were successfully saved.'

    settings_mock.load.assert_called_once()
    settings_mock.save.assert_called_once()
    settings_mock.__setitem__.assert_any_call('auth_backend', 'auth_basic')

    # The User row is the new source of truth for credentials.
    user = User.objects.get(username='testuser')
    assert user.check_password(_FIXTURE_PASSWORD)
    assert user.is_staff and user.is_superuser

    publisher_instance.send_to_viewer.assert_called_once_with('reload')


def _make_operator(username: str, pwd: str) -> User:
    """Single NOSONAR-suppressed call site for the test User factory
    so Sonar's S6437 doesn't fire on every test that needs an operator
    row."""
    return User.objects.create_superuser(
        username=username,
        password=pwd,  # NOSONAR
    )


@pytest.mark.django_db
@mock.patch('anthias_server.api.views.v2.settings')
@mock.patch('anthias_server.api.views.v2.ViewerPublisher')
def test_disable_basic_auth(
    publisher_mock: Any,
    settings_mock: Any,
    api_client: APIClient,
    device_settings_url: str,
) -> None:
    """Disabling auth flips ``auth_backend`` back to '' but keeps the
    User row intact so re-enabling later doesn't force a fresh
    password. Validating ``current_password`` is required — we drive
    the patch as the operator (force_authenticate) so the operator's
    credentials reach apply_auth_settings."""
    user = _make_operator(username='testuser', pwd=_FIXTURE_PASSWORD)
    api_client.force_authenticate(user=user)

    settings_mock.load = mock.MagicMock()
    settings_mock.save = mock.MagicMock()
    settings_mock.__getitem__.side_effect = lambda key: {
        'player_name': 'Test Player',
        'auth_backend': 'auth_basic',
        'audio_output': 'hdmi',
        'default_duration': '15',
        'default_streaming_duration': '100',
        'date_format': 'YYYY-MM-DD',
        'show_splash': True,
        'default_assets': [],
        'shuffle_playlist': False,
        'use_24_hour_clock': True,
        'debug_logging': False,
        'prefer_dark_mode': False,
        'screen_rotation': 0,
    }[key]
    settings_mock.__setitem__ = mock.MagicMock()

    publisher_instance = mock.MagicMock()
    publisher_mock.get_instance.return_value = publisher_instance

    data = {
        'auth_backend': '',
        'current_password': _FIXTURE_PASSWORD,
    }

    response = api_client.patch(device_settings_url, data=data, format='json')

    assert response.status_code == status.HTTP_200_OK
    assert response.data['message'] == 'Settings were successfully saved.'

    settings_mock.load.assert_called_once()
    settings_mock.save.assert_called_once()
    settings_mock.__setitem__.assert_any_call('auth_backend', '')

    # User row is preserved so the operator can flip auth back on
    # without resetting the password.
    assert User.objects.filter(username='testuser').exists()

    publisher_instance.send_to_viewer.assert_called_once_with('reload')

    response = api_client.get(device_settings_url)
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
@mock.patch('anthias_server.api.views.v2.settings')
@mock.patch('anthias_server.api.views.v2.ViewerPublisher')
@mock.patch('anthias_server.api.views.v2.add_default_assets')
@mock.patch('anthias_server.api.views.v2.remove_default_assets')
def test_patch_device_settings_default_assets(
    remove_default_assets_mock: Any,
    add_default_assets_mock: Any,
    publisher_mock: Any,
    settings_mock: Any,
    api_client: APIClient,
    device_settings_url: str,
) -> None:
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
        'default_assets': False,
        'shuffle_playlist': True,
        'use_24_hour_clock': False,
        'debug_logging': True,
    }[key]
    settings_mock.__setitem__ = mock.MagicMock()

    publisher_instance = mock.MagicMock()
    publisher_mock.get_instance.return_value = publisher_instance

    # Test enabling default assets
    data = {
        'default_assets': True,
    }

    response = api_client.patch(device_settings_url, data=data, format='json')

    assert response.status_code == status.HTTP_200_OK
    assert response.data['message'] == 'Settings were successfully saved.'

    settings_mock.load.assert_called_once()
    settings_mock.save.assert_called_once()
    settings_mock.__setitem__.assert_any_call('default_assets', True)
    add_default_assets_mock.assert_called_once()
    remove_default_assets_mock.assert_not_called()
    publisher_instance.send_to_viewer.assert_called_once_with('reload')

    # Reset mocks
    settings_mock.load.reset_mock()
    settings_mock.save.reset_mock()
    settings_mock.__setitem__.reset_mock()
    add_default_assets_mock.reset_mock()
    remove_default_assets_mock.reset_mock()
    publisher_instance.send_to_viewer.reset_mock()

    # Update mock to simulate default assets being enabled
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

    # Test disabling default assets
    data = {
        'default_assets': False,
    }

    response = api_client.patch(device_settings_url, data=data, format='json')

    assert response.status_code == status.HTTP_200_OK
    assert response.data['message'] == 'Settings were successfully saved.'

    settings_mock.load.assert_called_once()
    settings_mock.save.assert_called_once()
    settings_mock.__setitem__.assert_any_call('default_assets', False)
    remove_default_assets_mock.assert_called_once()
    add_default_assets_mock.assert_not_called()
    publisher_instance.send_to_viewer.assert_called_once_with('reload')


# Issue #2856 — Screen rotation is a UI-driven setting; the v2 patch
# must accept the four cardinal angles, persist the new value, and
# publish ``reload`` so the viewer picks the change up live.


@pytest.mark.django_db
@mock.patch('anthias_server.api.views.v2.settings')
@mock.patch('anthias_server.api.views.v2.ViewerPublisher')
def test_patch_device_settings_screen_rotation(
    publisher_mock: Any,
    settings_mock: Any,
    api_client: APIClient,
    device_settings_url: str,
) -> None:
    settings_mock.load = mock.MagicMock()
    settings_mock.save = mock.MagicMock()
    settings_mock.__getitem__.side_effect = lambda key: {
        'player_name': 'Test Player',
        'auth_backend': '',
    }[key]
    settings_mock.__setitem__ = mock.MagicMock()

    publisher_instance = mock.MagicMock()
    publisher_mock.get_instance.return_value = publisher_instance

    response = api_client.patch(
        device_settings_url, data={'screen_rotation': 90}, format='json'
    )

    assert response.status_code == status.HTTP_200_OK
    settings_mock.__setitem__.assert_any_call('screen_rotation', 90)
    publisher_instance.send_to_viewer.assert_called_once_with('reload')


@pytest.mark.django_db
@mock.patch('anthias_server.api.views.v2.settings')
def test_patch_device_settings_rotation_rejects_non_cardinal(
    settings_mock: Any, api_client: APIClient, device_settings_url: str
) -> None:
    """45 isn't one of the four supported angles — serializer must 400
    rather than passing it through to the viewer (where neither the Qt
    linuxfb plugin nor wlr-randr will honor it)."""
    response = api_client.patch(
        device_settings_url, data={'screen_rotation': 45}, format='json'
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'screen_rotation' in response.data
    settings_mock.load.assert_not_called()
    settings_mock.save.assert_not_called()


@pytest.fixture
def integrations_url() -> str:
    return reverse('api:integrations_v2')


@pytest.mark.django_db
@patch('anthias_server.api.views.v2.is_balena_app')
@patch('anthias_server.api.views.v2.getenv')
def test_integrations_balena_environment(
    mock_getenv: Any,
    mock_is_balena: Any,
    api_client: APIClient,
    integrations_url: str,
) -> None:
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

    response = api_client.get(integrations_url)
    assert response.status_code == 200
    assert response.json() == {
        'is_balena': True,
        'balena_device_id': 'test-device-uuid',
        'balena_app_id': 'test-app-id',
        'balena_app_name': 'test-app-name',
        'balena_supervisor_version': 'test-supervisor-version',
        'balena_host_os_version': 'test-host-os-version',
        'balena_device_name_at_init': 'test-device-name',
    }


@pytest.mark.django_db
@patch('anthias_server.api.views.v2.is_balena_app')
def test_integrations_non_balena_environment(
    mock_is_balena: Any, api_client: APIClient, integrations_url: str
) -> None:
    # Mock non-Balena environment
    mock_is_balena.side_effect = lambda: False

    response = api_client.get(integrations_url)
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


# --- Display power (experimental, HDMI-CEC) -------------------------


@pytest.mark.django_db
@mock.patch(
    'anthias_server.api.views.mixins.diagnostics.cec_available',
    return_value=True,
)
@mock.patch(
    'anthias_server.api.views.mixins.diagnostics.set_display_power',
    return_value=(True, 'Display turn-on command sent.'),
)
def test_display_power_on_success(
    set_display_power_mock: Any,
    _cec_available_mock: Any,
    api_client: APIClient,
) -> None:
    response = api_client.post(
        reverse('api:display_power_v2', kwargs={'state': 'on'})
    )
    assert response.status_code == status.HTTP_200_OK
    set_display_power_mock.assert_called_once_with(on=True)
    assert 'sent' in response.data['message']


@pytest.mark.django_db
@mock.patch(
    'anthias_server.api.views.mixins.diagnostics.cec_available',
    return_value=True,
)
@mock.patch(
    'anthias_server.api.views.mixins.diagnostics.set_display_power',
    return_value=(True, 'Display turn-off command sent.'),
)
def test_display_power_off_success(
    set_display_power_mock: Any,
    _cec_available_mock: Any,
    api_client: APIClient,
) -> None:
    response = api_client.post(
        reverse('api:display_power_v2', kwargs={'state': 'off'})
    )
    assert response.status_code == status.HTTP_200_OK
    set_display_power_mock.assert_called_once_with(on=False)


@pytest.mark.django_db
@mock.patch(
    'anthias_server.api.views.mixins.diagnostics.cec_available',
    return_value=True,
)
@mock.patch(
    'anthias_server.api.views.mixins.diagnostics.set_display_power',
    return_value=(False, 'Display turn-on failed: no adapter'),
)
def test_display_power_failure_returns_502(
    _set_display_power_mock: Any,
    _cec_available_mock: Any,
    api_client: APIClient,
) -> None:
    response = api_client.post(
        reverse('api:display_power_v2', kwargs={'state': 'on'})
    )
    assert response.status_code == status.HTTP_502_BAD_GATEWAY
    assert 'no adapter' in response.data['message']


@pytest.mark.django_db
@mock.patch('anthias_server.api.views.mixins.diagnostics.set_display_power')
def test_display_power_invalid_state_returns_400(
    set_display_power_mock: Any, api_client: APIClient
) -> None:
    response = api_client.post(
        reverse('api:display_power_v2', kwargs={'state': 'foo'})
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    set_display_power_mock.assert_not_called()


@pytest.mark.django_db
@mock.patch(
    'anthias_server.api.views.mixins.diagnostics.cec_available',
    return_value=False,
)
@mock.patch('anthias_server.api.views.mixins.diagnostics.set_display_power')
def test_display_power_returns_503_when_no_cec_adapter(
    set_display_power_mock: Any,
    _cec_available_mock: Any,
    api_client: APIClient,
) -> None:
    """The endpoint must fail fast (no 10 s subprocess) when neither
    /dev/cec0 nor /dev/vchiq exists. 503 telegraphs 'this server lacks
    the hardware to satisfy the request' more accurately than 502."""
    response = api_client.post(
        reverse('api:display_power_v2', kwargs={'state': 'on'})
    )
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    set_display_power_mock.assert_not_called()
    assert 'adapter' in response.data['message']


@pytest.mark.django_db
@mock.patch('anthias_server.api.views.v2.settings')
def test_patch_device_settings_password_mismatch_is_not_logged_as_error(
    settings_mock: Any,
    api_client: APIClient,
    device_settings_url: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A mismatched-password PATCH is operator input validation, not a
    server bug. It must return 400 with the operator-friendly message
    AND be logged at WARNING (no traceback) so Sentry's logging
    integration doesn't turn it into an event (ANTHIAS-3D)."""
    import logging

    user = _make_operator(username='testuser', pwd=_FIXTURE_PASSWORD)
    api_client.force_authenticate(user=user)

    settings_mock.load = mock.MagicMock()
    settings_mock.save = mock.MagicMock()
    settings_mock.__getitem__.side_effect = lambda key: {
        'auth_backend': 'auth_basic',
    }[key]
    settings_mock.__setitem__ = mock.MagicMock()

    data = {
        'auth_backend': 'auth_basic',
        'current_password': _FIXTURE_PASSWORD,
        'username': 'testuser',
        'password': 'brand-new-password',  # NOSONAR
        'password_2': 'does-not-match',  # NOSONAR
    }

    with caplog.at_level(logging.WARNING):
        response = api_client.patch(
            device_settings_url, data=data, format='json'
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    # The operator-friendly message is echoed, not a generic string.
    assert 'do not match' in response.data['error']
    # Nothing was persisted — the validation aborted before save().
    settings_mock.save.assert_not_called()

    save_records = [
        r for r in caplog.records if 'Settings save' in r.getMessage()
    ]
    assert save_records, 'expected a log line for the rejected save'
    # The rejection logs at WARNING, never ERROR, and carries no
    # exc_info — an ERROR record (logger.exception) is what becomes a
    # Sentry event.
    assert all(r.levelno == logging.WARNING for r in save_records)
    assert all(r.exc_info is None for r in save_records)
