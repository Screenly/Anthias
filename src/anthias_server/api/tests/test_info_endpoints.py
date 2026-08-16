"""
Tests for Info API endpoints (v1 and v2).
"""

from typing import Any
from unittest import mock

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from anthias_server.lib.diagnostics import get_anthias_release

# Pulled from pyproject.toml's [project].version via the diagnostics
# helper so the test moves in lockstep with the release bumper, and
# also works in environments built with `uv sync --no-install-project`
# (production, host install) where importlib.metadata wouldn't find
# the package — the helper falls back to a tomllib read.
_ANTHIAS_RELEASE = get_anthias_release()


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


def _assert_mock_calls(mocks: list[Any]) -> None:
    """Assert that all mocks were called exactly once."""
    for mock_obj in mocks:
        assert mock_obj.call_count == 1


def _assert_response_data(
    data: Any,
    expected_data: dict[str, Any],
) -> None:
    """Assert that the response data matches the expected data."""
    for key, expected_value in expected_data.items():
        assert data[key] == expected_value


@pytest.mark.django_db
@mock.patch(
    'anthias_server.api.views.mixins.is_up_to_date', return_value=False
)
@mock.patch(
    'anthias_server.lib.diagnostics.get_load_avg',
    return_value={'15 min': 0.11},
)
@mock.patch(
    'anthias_server.api.views.mixins.filesizeformat',
    return_value='15.0\xa0GB',
)
@mock.patch('anthias_server.api.views.mixins.statvfs', mock.MagicMock())
@mock.patch('anthias_server.api.views.mixins.r.get', return_value='off')
def test_info_v1_endpoint(
    redis_get_mock: Any,
    filesizeformat_mock: Any,
    get_load_avg_mock: Any,
    is_up_to_date_mock: Any,
    api_client: APIClient,
) -> None:
    info_url_v1 = reverse('api:info_v1')
    response = api_client.get(info_url_v1)
    data = response.data

    # Assert response status
    assert response.status_code == status.HTTP_200_OK

    # Assert mock calls
    _assert_mock_calls(
        [
            redis_get_mock,
            filesizeformat_mock,
            get_load_avg_mock,
            is_up_to_date_mock,
        ]
    )

    # Assert response data. ``free_space`` follows Django's
    # ``filesizeformat`` shape: "15.0\xa0GB" — a non-breaking space
    # between the number and unit. Replaces the previous "15G" emitted
    # by hurry.filesize, which was dropped to drop the dep.
    expected_data = {
        'viewlog': 'Not yet implemented',
        'loadavg': 0.11,
        'free_space': '15.0\xa0GB',
        'display_power': 'off',
        'up_to_date': False,
    }
    _assert_response_data(data, expected_data)


@pytest.mark.django_db
@mock.patch('anthias_server.api.views.v2.is_up_to_date', return_value=True)
@mock.patch(
    'anthias_server.lib.diagnostics.get_load_avg',
    return_value={'15 min': 0.25},
)
@mock.patch(
    'anthias_server.api.views.v2.filesizeformat',
    return_value='20.0\xa0GB',
)
@mock.patch('anthias_server.api.views.v2.statvfs', mock.MagicMock())
@mock.patch('anthias_server.api.views.v2.r.get', return_value='on')
# Stubbed with an explicit `new` so it injects no extra argument.
# Without it the storage probe reads its Redis latch, which is a
# second `r.get` and would trip the call_count == 1 assertion below —
# an assertion about the display-power read, not about this.
@mock.patch(
    'anthias_server.api.views.v2.storage_health.get_state',
    mock.MagicMock(return_value={'supported': False, 'status': 'unknown'}),
)
@mock.patch(
    'anthias_server.api.views.v2.diagnostics.get_git_branch',
    return_value='main',
)
@mock.patch(
    'anthias_server.api.views.v2.diagnostics.get_git_short_hash',
    return_value='a1b2c3d',
)
@mock.patch(
    'anthias_server.api.views.v2.device_helper.parse_cpu_info',
    return_value={'model': 'Raspberry Pi 4'},
)
@mock.patch(
    'anthias_server.api.views.v2.diagnostics.get_uptime', return_value=86400
)
@mock.patch(
    'anthias_server.api.views.v2.psutil.virtual_memory',
    return_value=mock.MagicMock(
        total=8192 << 20,  # 8GB
        used=4096 << 20,  # 4GB
        free=4096 << 20,  # 4GB
        shared=0,
        buffers=1024 << 20,  # 1GB
        available=7168 << 20,  # 7GB
    ),
)
@mock.patch(
    'anthias_server.api.views.v2.get_node_mac_address',
    return_value='00:11:22:33:44:55',
)
@mock.patch(
    'anthias_server.api.views.v2.get_node_ip',
    return_value='192.168.1.100 10.0.0.50',
)
@mock.patch('anthias_server.api.views.v2.getenv', return_value='testuser')
def test_info_v2_endpoint(
    getenv_mock: Any,
    get_node_ip_mock: Any,
    mac_address_mock: Any,
    virtual_memory_mock: Any,
    get_uptime_mock: Any,
    parse_cpu_info_mock: Any,
    get_git_short_hash_mock: Any,
    get_git_branch_mock: Any,
    redis_get_mock: Any,
    filesizeformat_mock: Any,
    get_load_avg_mock: Any,
    is_up_to_date_mock: Any,
    api_client: APIClient,
) -> None:
    info_url_v2 = reverse('api:info_v2')
    response = api_client.get(info_url_v2)
    data = response.data

    # Assert response status
    assert response.status_code == status.HTTP_200_OK

    # Assert mock calls
    _assert_mock_calls(
        [
            redis_get_mock,
            filesizeformat_mock,
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
        ]
    )

    # Assert response data
    expected_data = {
        'viewlog': 'Not yet implemented',
        'loadavg': 0.25,
        'free_space': '20.0\xa0GB',
        'display_power': 'on',
        'up_to_date': True,
        # Version label format is `v{calver} ({short_hash})` on a
        # release branch (`main`/`master`); the branch is suppressed
        # in that case because operators don't need to see "you are on
        # the release branch". lib.diagnostics composes the label from
        # importlib.metadata.version('anthias') + GIT_SHORT_HASH.
        'anthias_version': f'v{_ANTHIAS_RELEASE} (a1b2c3d)',
        'device_model': 'Raspberry Pi 4',
        'uptime': {'days': 1, 'hours': 0.0},
        'memory': {
            'total': 8192,
            'used': 4096,
            'free': 4096,
            'shared': 0,
            'buff': 1024,
            'available': 7168,
            # mock total=8 GiB is well above the 1.5 GiB low-RAM
            # cutoff, so the gate is inactive on this synthetic
            # device.
            'low_ram': False,
        },
        'ip_addresses': ['http://192.168.1.100', 'http://10.0.0.50'],
        'mac_address': '00:11:22:33:44:55',
        'host_user': 'testuser',
    }
    _assert_response_data(data, expected_data)


# ---------------------------------------------------------------------------
# under_voltage on /api/v2/info
#
# Power-supply health, read from the kernel rpi_volt hwmon sensor. See
# anthias_common.undervoltage for why the firmware mailbox is not used.
# ---------------------------------------------------------------------------


def _patch_under_voltage(**overrides: Any) -> Any:
    state = {
        'supported': True,
        'active': False,
        'seen_since_boot': False,
        'first_seen': None,
        'last_seen': None,
        'count': 0,
    }
    state.update(overrides)
    return mock.patch(
        'anthias_server.api.views.v2.undervoltage.get_state',
        return_value=state,
    )


@pytest.mark.django_db
def test_info_v2_reports_a_healthy_supply(api_client: APIClient) -> None:
    with _patch_under_voltage():
        response = api_client.get(reverse('api:info_v2'))

    assert response.status_code == status.HTTP_200_OK
    assert response.data['under_voltage'] == {
        'supported': True,
        'active': False,
        'seen_since_boot': False,
        'first_seen': None,
        'last_seen': None,
        'count': 0,
    }


@pytest.mark.django_db
def test_info_v2_reports_a_live_brown_out(api_client: APIClient) -> None:
    with _patch_under_voltage(
        active=True,
        seen_since_boot=True,
        count=4,
        first_seen='2026-08-15T10:00:00+00:00',
        last_seen='2026-08-15T10:07:00+00:00',
    ):
        response = api_client.get(reverse('api:info_v2'))

    under_voltage = response.data['under_voltage']
    assert under_voltage['active'] is True
    assert under_voltage['count'] == 4
    # ISO-8601 strings, matching how the latch stores them.
    assert under_voltage['last_seen'] == '2026-08-15T10:07:00+00:00'


@pytest.mark.django_db
def test_info_v2_distinguishes_unsupported_from_healthy(
    api_client: APIClient,
) -> None:
    # A board with no sensor reports the same falsey values as a
    # healthy one, so `supported` is the only thing separating "we
    # checked and it's fine" from "we can't check". Clients must be
    # able to tell them apart.
    with _patch_under_voltage(supported=False):
        response = api_client.get(reverse('api:info_v2'))

    assert response.data['under_voltage']['supported'] is False


@pytest.mark.django_db
def test_info_v2_survives_an_unreadable_sensor(
    api_client: APIClient,
) -> None:
    # A diagnostic must never take the info endpoint down with it.
    with mock.patch(
        'anthias_server.api.views.v2.undervoltage.get_state',
        side_effect=OSError('sysfs went away'),
    ):
        response = api_client.get(reverse('api:info_v2'))

    assert response.status_code == status.HTTP_200_OK
    assert response.data['under_voltage']['supported'] is False


# ---------------------------------------------------------------------------
# storage on /api/v2/info
#
# SD cards have no health register, so this is a verdict assembled from
# ext4's superblock counters, a periodic write check and the eMMC wear
# registers. See anthias_common.storage_health.
# ---------------------------------------------------------------------------


def _patch_storage(**overrides: Any) -> Any:
    state: dict[str, Any] = {
        'supported': True,
        'status': 'ok',
        'mount_point': '/data/.anthias',
        'fstype': 'ext4',
        'device': 'mmcblk0p2',
        'disk': 'mmcblk0',
        'read_only': False,
        'error_stats_supported': True,
        'errors_count': 0,
        'errors_new': 0,
        'errors_this_boot': False,
        'first_error': None,
        'last_error': None,
        'last_error_function': None,
        'lifetime_written_kb': 4194304,
        'write_ok': True,
        'write_reason': None,
        'write_failed_since_boot': False,
        'write_fail_count': 0,
        'first_write_fail': None,
        'last_write_fail': None,
        'last_check': '2026-08-15T10:00:00+00:00',
        'fsync_ms': 2.4,
        'media': {
            'kind': 'sd',
            'name': 'SC32G',
            'manufacturer': 'SanDisk',
            'manufactured': '03/2019',
            'wear_pct': None,
            'pre_eol': None,
        },
    }
    state.update(overrides)
    return mock.patch(
        'anthias_server.api.views.v2.storage_health.get_state',
        return_value=state,
    )


@pytest.mark.django_db
def test_info_v2_reports_healthy_storage(api_client: APIClient) -> None:
    with _patch_storage():
        response = api_client.get(reverse('api:info_v2'))

    assert response.status_code == status.HTTP_200_OK
    storage = response.data['storage']
    assert storage['status'] == 'ok'
    assert storage['device'] == 'mmcblk0p2'
    assert storage['media']['kind'] == 'sd'


@pytest.mark.django_db
def test_info_v2_reports_a_failing_card(api_client: APIClient) -> None:
    with _patch_storage(
        status='failing',
        read_only=True,
        errors_count=12,
        errors_new=3,
        errors_this_boot=True,
        last_error='2026-08-15T09:58:00+00:00',
        last_error_function='ext4_find_entry',
    ):
        response = api_client.get(reverse('api:info_v2'))

    storage = response.data['storage']
    assert storage['status'] == 'failing'
    assert storage['read_only'] is True
    assert storage['errors_new'] == 3
    # ISO-8601 strings, matching the under_voltage field and the latch.
    assert storage['last_error'] == '2026-08-15T09:58:00+00:00'


@pytest.mark.django_db
def test_info_v2_separates_a_full_card_from_a_failing_one(
    api_client: APIClient,
) -> None:
    # Both fail writes, and a client that lumped them together would
    # tell someone to replace hardware that is working fine.
    with _patch_storage(
        status='full', write_ok=False, write_reason='no_space'
    ):
        response = api_client.get(reverse('api:info_v2'))

    assert response.data['storage']['status'] == 'full'


@pytest.mark.django_db
def test_info_v2_exposes_emmc_wear(api_client: APIClient) -> None:
    with _patch_storage(
        status='wear',
        media={
            'kind': 'emmc',
            'name': 'DG4008',
            'manufacturer': 'Samsung',
            'manufactured': '06/2021',
            'wear_pct': 90,
            'pre_eol': 'warning',
        },
    ):
        response = api_client.get(reverse('api:info_v2'))

    media = response.data['storage']['media']
    assert media['kind'] == 'emmc'
    assert media['wear_pct'] == 90
    assert media['pre_eol'] == 'warning'


@pytest.mark.django_db
def test_info_v2_distinguishes_unchecked_storage_from_healthy(
    api_client: APIClient,
) -> None:
    with _patch_storage(supported=False, status='unknown'):
        response = api_client.get(reverse('api:info_v2'))

    assert response.data['storage']['supported'] is False
    assert response.data['storage']['status'] == 'unknown'


@pytest.mark.django_db
def test_info_v2_survives_an_unreadable_filesystem(
    api_client: APIClient,
) -> None:
    # A diagnostic must never take the info endpoint down with it.
    with mock.patch(
        'anthias_server.api.views.v2.storage_health.get_state',
        side_effect=OSError('sysfs went away'),
    ):
        response = api_client.get(reverse('api:info_v2'))

    assert response.status_code == status.HTTP_200_OK
    assert response.data['storage']['supported'] is False
