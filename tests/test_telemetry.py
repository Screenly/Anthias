import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout

from anthias_server.app.models import Asset
from anthias_server.lib import telemetry


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
    fake.set.side_effect = lambda key, value, ex=None: redis_data.__setitem__(
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
    # The released CalVer, which GA4's "Version distribution" report
    # reads via its version_name dimension. Never sent before, so that
    # report showed ~3,700 devices as "(not reported)".
    assert params['version_name']
    assert params['version_name'] != 'unknown'


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
    # Value and TTL are written in a single atomic SET so a soft time
    # limit can't strand the cooldown key without an expiry.
    fake_redis.set.assert_any_call(
        telemetry.TELEMETRY_COOLDOWN_KEY,
        '1',
        ex=telemetry.TELEMETRY_COOLDOWN_TTL,
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


@patch.object(telemetry, 'requests_post')
def test_version_name_carries_the_released_calver(
    mock_post: Any, telemetry_env: None
) -> None:
    """GA4's "Version distribution" report keys on `version_name`, and
    nothing ever sent it — not this payload, and not the pre-#2798 one
    either, whose `Pi_Version` was the *hardware* model. So the report
    could only ever show "(not reported)".

    Sourced from get_anthias_release() (pyproject.toml, shipped in the
    image) rather than an env var, because env vars are the known
    failure mode for telemetry params."""
    with patch.object(
        telemetry, 'get_anthias_release', return_value='2026.8.0'
    ):
        assert telemetry.send_telemetry() is True
    params = json.loads(mock_post.call_args.kwargs['data'])['events'][0][
        'params'
    ]
    assert params['version_name'] == '2026.8.0'


@patch.object(telemetry, 'requests_post')
def test_version_name_falls_back_to_an_explicit_bucket(
    mock_post: Any, telemetry_env: None
) -> None:
    """get_anthias_release() returns '' when both its sources fail. Send
    'unknown' rather than the empty string, so such a device shows up as
    its own bucket in GA4 instead of silently rejoining the
    "(not reported)" pile that hid this bug in the first place."""
    with patch.object(telemetry, 'get_anthias_release', return_value=''):
        assert telemetry.send_telemetry() is True
    params = json.loads(mock_post.call_args.kwargs['data'])['events'][0][
        'params'
    ]
    assert params['version_name'] == 'unknown'


def test_asset_counts_let_a_soft_time_limit_through() -> None:
    """The blanket "degrade to zeros" arm must not eat celery's one
    soft-limit delivery: swallowing it here left
    ``send_telemetry_task``'s handler unreachable and the pool child to
    be SIGKILLed at the 60s hard limit (Sentry ANTHIAS-A / 9 / B)."""
    with (
        patch.object(
            Asset.objects,
            'filter',
            side_effect=SoftTimeLimitExceeded(),
        ),
        pytest.raises(SoftTimeLimitExceeded),
    ):
        telemetry._get_asset_counts()


def test_asset_counts_still_degrade_to_zeros_on_a_db_fault() -> None:
    with patch.object(Asset.objects, 'filter', side_effect=OSError('no db')):
        assert telemetry._get_asset_counts()['asset_count'] == 0


@patch.object(telemetry, 'requests_post')
def test_device_active_reports_multi_screen_role(
    mock_post: Any, telemetry_env: None
) -> None:
    with patch.object(
        telemetry, '_multi_screen_role', return_value=(False, True)
    ):
        assert telemetry.send_telemetry() is True
    payload = json.loads(mock_post.call_args.kwargs['data'])
    assert [e['name'] for e in payload['events']] == ['device_active']
    params = payload['events'][0]['params']
    assert params['is_controller'] is False
    assert params['is_managed'] is True
    # GA4 drops events with more than 25 parameters.
    assert len(params) <= 25


@patch.object(telemetry, 'requests_post')
def test_controller_adds_an_anonymous_controller_health_event(
    mock_post: Any, telemetry_env: None
) -> None:
    counts = {'screens_total': 3, 'screens_online': 2, 'screens_low_disk': 1}
    with (
        patch.object(
            telemetry, '_multi_screen_role', return_value=(True, False)
        ),
        patch(
            'anthias_server.controller.health.cluster_summary',
            return_value=counts,
        ),
        patch('anthias_server.controller.models.Node.objects'),
    ):
        assert telemetry.send_telemetry() is True
    payload = json.loads(mock_post.call_args.kwargs['data'])
    names = [e['name'] for e in payload['events']]
    assert names == ['device_active', 'controller_health']
    assert payload['events'][0]['params']['is_controller'] is True
    assert payload['events'][1]['params'] == counts


@patch.object(telemetry, 'requests_post')
def test_controller_health_failure_still_sends_device_active(
    mock_post: Any, telemetry_env: None
) -> None:
    with (
        patch.object(
            telemetry, '_multi_screen_role', return_value=(True, False)
        ),
        patch(
            'anthias_server.controller.health.cluster_summary',
            side_effect=RuntimeError('db gone'),
        ),
        patch('anthias_server.controller.models.Node.objects'),
    ):
        assert telemetry.send_telemetry() is True
    payload = json.loads(mock_post.call_args.kwargs['data'])
    assert [e['name'] for e in payload['events']] == ['device_active']


@patch.object(telemetry, 'requests_post')
def test_opt_out_sends_no_controller_health_either(
    mock_post: Any, telemetry_env: None, settings_data: dict[str, Any]
) -> None:
    settings_data['analytics_opt_out'] = True
    with patch.object(
        telemetry, '_multi_screen_role', return_value=(True, False)
    ):
        assert telemetry.send_telemetry() is False
    mock_post.assert_not_called()


def test_multi_screen_role_degrades_to_false_on_a_fault() -> None:
    with patch(
        'anthias_server.lib.management.is_managed',
        side_effect=RuntimeError('db gone'),
    ):
        assert telemetry._multi_screen_role() == (False, False)


def test_multi_screen_role_lets_a_soft_time_limit_through() -> None:
    with (
        patch(
            'anthias_server.lib.management.is_managed',
            side_effect=SoftTimeLimitExceeded(),
        ),
        pytest.raises(SoftTimeLimitExceeded),
    ):
        telemetry._multi_screen_role()


@pytest.mark.django_db
def test_controller_health_event_reads_stored_readings() -> None:
    from django.utils import timezone

    from anthias_server.controller import health
    from anthias_server.controller.models import Node, NodeStatus

    Node.objects.create(
        name='Lobby',
        host='lobby.lan',
        fingerprint='a' * 64,
        token_ciphertext='x',
        status=NodeStatus.ONLINE,
        health=health.health_from_info(
            {'free_space': '200.0\xa0MB', 'storage': {'status': 'ok'}}
        ),
        health_at=timezone.now(),
    )
    event = telemetry._controller_health_event()
    assert event is not None
    params = event['params']
    assert isinstance(params, dict)
    assert params['screens_total'] == 1
    assert params['screens_low_disk'] == 1
    # Counts only: nothing that identifies a screen.
    assert all(isinstance(v, int) for v in params.values())
    assert 'lobby' not in json.dumps(event).lower()
