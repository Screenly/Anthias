"""Controller: health of managed screens (disk, storage, power, memory)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any, Self

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from anthias_server.controller import health, service, vault
from anthias_server.controller.models import Node, NodeStatus

_PWD = 'fixture-controller-health-pwd'  # NOSONAR

# Trimmed from a real Pi 3B+ answering GET /api/v2/info on the QA rig.
INFO: dict[str, Any] = {
    'loadavg': 1.16,
    'free_space': '17.9\xa0GB',
    'display_power': 'No CEC display detected',
    'anthias_version': 'v2026.11.1 (6b96ee1)',
    'device_model': 'Raspberry Pi 3 Model B Plus Rev 1.3',
    'uptime': {'days': 2, 'hours': 1.17},
    'memory': {'total': 787, 'available': 217, 'low_ram': True},
    'under_voltage': {
        'supported': True,
        'active': False,
        'seen_since_boot': False,
    },
    'storage': {'supported': True, 'status': 'ok'},
}


@pytest.fixture(autouse=True)
def _vault_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    monkeypatch.setattr(vault, 'key_path', lambda: str(tmp_path / 'k.key'))
    vault.reset_cache()
    yield
    vault.reset_cache()


def _node(name: str, **fields: Any) -> Node:
    fields.setdefault('status', NodeStatus.ONLINE)
    return Node.objects.create(
        name=name,
        host=f'{name.lower()}.lan',
        fingerprint=(name.lower() * 64)[:64],
        token_ciphertext=vault.encrypt('anthias_x'),
        **fields,
    )


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('17.9\xa0GB', int(17.9 * 1024**3)),
        ('512.0 MB', 512 * 1024**2),
        ('3,5 GB', int(3.5 * 1024**3)),
        ('1\xa0byte', 1),
        ('10 bytes', 10),
        ('2.0 TB', 2 * 1024**4),
        ('', None),
        ('lots', None),
        ('5 XB', None),
        (None, None),
        (42, None),
    ],
)
def test_parse_size(text: Any, expected: int | None) -> None:
    assert health.parse_size(text) == expected


def test_health_from_info_keeps_the_useful_fields() -> None:
    reading = health.health_from_info(INFO)
    assert reading['free_space'] == '17.9 GB'
    assert reading['free_space_bytes'] == int(17.9 * 1024**3)
    assert reading['memory_total_mib'] == 787
    assert reading['memory_available_mib'] == 217
    assert reading['loadavg'] == 1.16
    assert reading['uptime_hours'] == 49.2
    assert reading['under_voltage_supported'] is True
    assert reading['under_voltage_active'] is False
    assert reading['storage_status'] == 'ok'
    assert reading['display_power'] == 'No CEC display detected'
    assert health.problems(reading) == []


def test_health_from_info_tolerates_an_older_or_odd_screen() -> None:
    reading = health.health_from_info(
        {'memory': 'n/a', 'loadavg': float('nan'), 'uptime': {'days': 1}}
    )
    assert reading['free_space_bytes'] is None
    assert reading['memory_available_mib'] is None
    assert reading['loadavg'] is None
    assert reading['uptime_hours'] is None
    assert reading['storage_status'] == 'unknown'
    assert reading['under_voltage_active'] is False
    assert health.problems(reading) == []


def test_problems_name_each_warning() -> None:
    reading = health.health_from_info(
        {
            **INFO,
            'free_space': '512.0\xa0MB',
            'memory': {'total': 787, 'available': 40},
            'under_voltage': {'supported': True, 'active': True},
            'storage': {'status': 'failing'},
        }
    )
    assert health.problems(reading) == [
        ('low_disk', 'Low disk space: 512.0 MB free'),
        ('storage', 'Storage failing'),
        ('under_voltage', 'Under-voltage now'),
        ('low_memory', 'Low memory: 40.0\xa0MB free'),
    ]


def test_under_voltage_earlier_in_this_boot_is_still_reported() -> None:
    reading = health.health_from_info(
        {**INFO, 'under_voltage': {'active': False, 'seen_since_boot': True}}
    )
    assert health.problems(reading) == [
        ('under_voltage', 'Under-voltage since boot')
    ]


def test_summary_line() -> None:
    reading = health.health_from_info(INFO)
    assert health.summary_line(reading) == (
        'Disk 17.9 GB free · RAM 217.0\xa0MB free · up 2 days'
    )
    assert health.summary_line({'uptime_hours': 0.4}) == 'up <1 h'
    assert health.summary_line({'uptime_hours': 5.6}) == 'up 6 h'


class _FakeClient:
    def __init__(self, info: dict[str, Any]) -> None:
        self.info = info

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def json(self, method: str, path: str, **_kw: Any) -> dict[str, Any]:
        assert (method, path) == ('GET', '/info')
        return self.info


@pytest.mark.django_db
def test_check_node_stores_the_health_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = _node('Lobby', status=NodeStatus.UNKNOWN)
    monkeypatch.setattr(service, 'client_for', lambda _n: _FakeClient(INFO))

    service.check_node(node)

    node.refresh_from_db()
    assert node.status == NodeStatus.ONLINE
    assert node.health['free_space_bytes'] == int(17.9 * 1024**3)
    assert node.health_at is not None
    assert node.health_fresh
    assert node.health_summary.startswith('Disk 17.9 GB free')
    assert node.health_problems == []


@pytest.mark.django_db
def test_stale_or_offline_readings_are_not_shown_or_counted() -> None:
    now = timezone.now()
    bad = health.health_from_info({**INFO, 'storage': {'status': 'errors'}})
    stale = _node('Stale', health=bad, health_at=now - timedelta(hours=1))
    offline = _node(
        'Gone', status=NodeStatus.OFFLINE, health=bad, health_at=now
    )
    assert stale.health_problems == [] and stale.health_summary == ''
    assert offline.health_problems == []
    summary = health.cluster_summary([stale, offline], now)
    assert summary['screens_with_health'] == 0
    assert summary['screens_storage_problem'] == 0
    assert summary['screens_online'] == 1
    assert summary['screens_offline'] == 1


@pytest.mark.django_db
def test_cluster_summary_counts_statuses_publishes_and_problems() -> None:
    now = timezone.now()
    healthy = health.health_from_info(INFO)
    sick = health.health_from_info(
        {
            **INFO,
            'free_space': '100.0\xa0MB',
            'under_voltage': {'active': True},
            'storage': {'status': 'wear'},
        }
    )
    nodes = [
        _node('A', health=healthy, health_at=now, publish_state='ok'),
        _node('B', health=sick, health_at=now, publish_state='failed'),
        _node('C', status=NodeStatus.UNAUTHORIZED),
        _node('D', status=NodeStatus.WRONG_CERT),
        _node('E', status=NodeStatus.UNKNOWN),
    ]
    assert health.cluster_summary(nodes, now) == {
        'screens_total': 5,
        'screens_online': 2,
        'screens_offline': 0,
        'screens_left': 1,
        'screens_cert_changed': 1,
        'screens_unknown': 1,
        'screens_publish_ok': 1,
        'screens_publish_failed': 1,
        'screens_with_health': 2,
        'screens_low_disk': 1,
        'screens_storage_problem': 1,
        'screens_under_voltage': 1,
        'screens_low_memory': 0,
    }


@pytest.mark.django_db
def test_screens_page_shows_health_and_warnings(
    _isolated_settings_conf: None,
) -> None:
    from anthias_server.settings import settings

    settings['auth_backend'] = 'auth_basic'
    settings['controller_mode'] = True
    settings.save()
    client = Client()
    client.force_login(
        User.objects.create_superuser(username='operator', password=_PWD)
    )
    now = timezone.now()
    _node('Lobby', health=health.health_from_info(INFO), health_at=now)
    _node(
        'Hall',
        health=health.health_from_info({**INFO, 'free_space': '300.0\xa0MB'}),
        health_at=now,
    )

    body = client.get(reverse('controller:screens_table')).content.decode()

    assert 'Disk 17.9 GB free · RAM 217.0\xa0MB free · up 2 days' in body
    assert 'Low disk space: 300.0 MB free' in body
    assert '1 needs attention' in body


def test_status_poll_timing_holds_together() -> None:
    from anthias_server import celery_tasks
    from anthias_server.controller import tasks

    # Polled every five minutes, not every minute: status and health
    # change slowly, and each poll costs every screen an /info call.
    assert tasks.STATUS_INTERVAL_S == 300
    # Two constants (the schedule lives in celery_tasks, the task in
    # controller.tasks); they must not drift apart.
    assert celery_tasks.CONTROLLER_STATUS_INTERVAL_S == tasks.STATUS_INTERVAL_S
    # A run finishes before the next one starts.
    assert (
        tasks.STATUS_SOFT_TIME_LIMIT_S
        < tasks.STATUS_TIME_LIMIT_S
        < tasks.STATUS_INTERVAL_S
    )
    # One missed poll doesn't make a reading stale.
    assert health.HEALTH_MAX_AGE.total_seconds() >= 2 * tasks.STATUS_INTERVAL_S
