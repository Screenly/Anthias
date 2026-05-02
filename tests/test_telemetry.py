import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout

from lib import telemetry


@pytest.fixture
def redis_data() -> dict[str, str]:
    return {}


@pytest.fixture
def settings_data() -> dict[str, Any]:
    return {
        'analytics_opt_out': False,
        'resolution': '1920x1080',
        'audio_output': 'hdmi',
        'use_ssl': False,
    }


@pytest.fixture
def fake_redis(redis_data: dict[str, str]) -> MagicMock:
    fake = MagicMock()
    fake.get.side_effect = redis_data.get
    fake.set.side_effect = lambda key, value: redis_data.__setitem__(
        key, value
    )
    fake.expire.side_effect = lambda key, _ttl: None
    return fake


@pytest.fixture
def telemetry_env(
    fake_redis: MagicMock,
    settings_data: dict[str, Any],
) -> Iterator[None]:
    patches = [
        patch.object(telemetry, 'r', fake_redis),
        patch.object(telemetry, 'is_ci', return_value=False),
        patch.object(telemetry, 'is_balena_app', return_value=False),
        patch.object(telemetry, 'get_git_branch', return_value='master'),
        patch.object(telemetry, 'get_git_short_hash', return_value='abc1234'),
        patch.object(
            telemetry, 'parse_cpu_info', return_value={'model': 'Pi 4'}
        ),
    ]
    settings_patch = patch.object(telemetry, 'settings')
    mock_settings = settings_patch.start()
    mock_settings.__getitem__.side_effect = settings_data.__getitem__
    for p in patches:
        p.start()
    try:
        yield
    finally:
        settings_patch.stop()
        for p in patches:
            p.stop()


@patch.object(telemetry, 'requests_post')
def test_sends_event_when_no_cooldown(
    mock_post: Any, telemetry_env: None
) -> None:
    with patch.dict('os.environ', {'DEVICE_TYPE': 'pi4-64'}):
        assert telemetry.send_telemetry() is True

    mock_post.assert_called_once()
    call_args = mock_post.call_args
    url = call_args.args[0]
    assert 'measurement_id=' in url
    assert 'api_secret=' in url

    payload = json.loads(call_args.kwargs['data'])
    assert len(payload['client_id']) == 15
    event = payload['events'][0]
    assert event['name'] == 'device_active'
    params = event['params']
    assert params['branch'] == 'master'
    assert params['commit_short'] == 'abc1234'
    assert params['device_type'] == 'pi4-64'
    assert params['hardware_model'] == 'Pi 4'
    # NOOBS is gone.
    assert 'is_noobs' not in params
    assert 'NOOBS' not in params
    # Asset counts always present even when DB is empty.
    assert 'asset_count' in params
    assert 'asset_image_count' in params
    assert 'asset_video_count' in params
    assert 'asset_webpage_count' in params
    # Display + TLS adoption.
    assert params['resolution'] == '1920x1080'
    assert params['audio_output'] == 'hdmi'
    assert params['tls_enabled'] is False


@patch.object(telemetry, 'requests_post')
def test_passes_timeout(mock_post: Any, telemetry_env: None) -> None:
    telemetry.send_telemetry()
    assert mock_post.call_args.kwargs['timeout'] == telemetry.ANALYTICS_TIMEOUT


@patch.object(telemetry, 'requests_post')
def test_skips_when_cooldown_set(
    mock_post: Any,
    telemetry_env: None,
    redis_data: dict[str, str],
) -> None:
    redis_data[telemetry.TELEMETRY_COOLDOWN_KEY] = '1'
    assert telemetry.send_telemetry() is False
    mock_post.assert_not_called()


@patch.object(telemetry, 'requests_post')
def test_skips_when_opted_out(
    mock_post: Any,
    telemetry_env: None,
    settings_data: dict[str, Any],
) -> None:
    settings_data['analytics_opt_out'] = True
    assert telemetry.send_telemetry() is False
    mock_post.assert_not_called()


@patch.object(telemetry, 'requests_post')
def test_skips_in_ci(mock_post: Any, telemetry_env: None) -> None:
    with patch.object(telemetry, 'is_ci', return_value=True):
        assert telemetry.send_telemetry() is False
    mock_post.assert_not_called()


@patch.object(telemetry, 'requests_post', side_effect=RequestsConnectionError)
def test_swallows_connection_error_and_skips_cooldown(
    _mock_post: Any,
    telemetry_env: None,
    redis_data: dict[str, str],
) -> None:
    assert telemetry.send_telemetry() is False
    # No cooldown set — next tick retries.
    assert telemetry.TELEMETRY_COOLDOWN_KEY not in redis_data


@patch.object(telemetry, 'requests_post', side_effect=Timeout)
def test_swallows_timeout(
    _mock_post: Any,
    telemetry_env: None,
    redis_data: dict[str, str],
) -> None:
    assert telemetry.send_telemetry() is False
    assert telemetry.TELEMETRY_COOLDOWN_KEY not in redis_data


@patch.object(telemetry, 'requests_post')
def test_sets_cooldown_after_success(
    _mock_post: Any,
    telemetry_env: None,
    redis_data: dict[str, str],
    fake_redis: MagicMock,
) -> None:
    telemetry.send_telemetry()
    assert telemetry.TELEMETRY_COOLDOWN_KEY in redis_data
    fake_redis.expire.assert_any_call(
        telemetry.TELEMETRY_COOLDOWN_KEY,
        telemetry.TELEMETRY_COOLDOWN_TTL,
    )


@patch.object(telemetry, 'requests_post')
def test_reuses_persisted_device_id(
    mock_post: Any,
    telemetry_env: None,
    redis_data: dict[str, str],
) -> None:
    redis_data[telemetry.DEVICE_ID_KEY] = 'persisted-id-123'
    telemetry.send_telemetry()
    payload = json.loads(mock_post.call_args.kwargs['data'])
    assert payload['client_id'] == 'persisted-id-123'


@patch.object(telemetry, 'requests_post')
def test_generates_and_persists_new_device_id(
    _mock_post: Any,
    telemetry_env: None,
    redis_data: dict[str, str],
) -> None:
    assert telemetry.DEVICE_ID_KEY not in redis_data
    telemetry.send_telemetry()
    assert telemetry.DEVICE_ID_KEY in redis_data
    assert (
        len(redis_data[telemetry.DEVICE_ID_KEY]) == telemetry.DEVICE_ID_LENGTH
    )
