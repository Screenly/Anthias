"""Tests for SMART collection and its normalization.

The fixtures are trimmed real ``smartctl --json`` documents. The
unsupported one is the genuine output this dev host produces for a
virtio disk, captured rather than invented.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
from typing import Any
from unittest import mock

import pytest

from anthias_common import smart

# What smartctl actually emits for a device it cannot talk to: no
# `smart_status`, an error message, exit_status 1.
UNSUPPORTED = {
    'json_format_version': [1, 0],
    'smartctl': {
        'version': [7, 4],
        'messages': [
            {
                'string': '/dev/vda: Unable to detect device type',
                'severity': 'error',
            }
        ],
        'exit_status': 1,
    },
}

NVME_HEALTHY = {
    'smartctl': {'exit_status': 0},
    'model_name': 'Samsung SSD 980 500GB',
    'firmware_version': '1B4QFXO7',
    'smart_status': {'passed': True},
    'temperature': {'current': 34},
    'nvme_smart_health_information_log': {
        'critical_warning': 0,
        'percentage_used': 3,
        'available_spare': 100,
        'available_spare_threshold': 10,
        'media_errors': 0,
        'power_on_hours': 4210,
    },
}

ATA_WORN = {
    'smartctl': {'exit_status': 0},
    'model_name': 'Crucial CT250MX500SSD1',
    'firmware_version': 'M3CR023',
    'smart_status': {'passed': True},
    'temperature': {'current': 41},
    'ata_smart_attributes': {
        'table': [
            {
                'id': 5,
                'name': 'Reallocated_Sector_Ct',
                'value': 100,
                'raw': {'value': 12},
            },
            {
                'id': 9,
                'name': 'Power_On_Hours',
                'value': 99,
                'raw': {'value': 31000},
            },
            {
                'id': 197,
                'name': 'Current_Pending_Sector',
                'value': 100,
                'raw': {'value': 3},
            },
            {
                'id': 202,
                'name': 'Percent_Lifetime_Remain',
                'value': 12,
                'raw': {'value': 88},
            },
        ]
    },
}


class TestParse:
    def test_device_without_smart_is_unsupported(self) -> None:
        state = smart.parse(UNSUPPORTED, '/dev/vda')

        assert state['supported'] is False
        assert state['wear_pct'] is None
        # Unsupported must not read as healthy. A virtio disk, a USB
        # bridge and an SD card all land here, and none of them is
        # evidence that the storage is fine.
        assert state['passed'] is None

    def test_empty_document_is_unsupported(self) -> None:
        assert smart.parse({}, '/dev/sda')['supported'] is False

    def test_nvme_wear_is_read_straight_through(self) -> None:
        state = smart.parse(NVME_HEALTHY, '/dev/nvme0n1')

        assert state['supported'] is True
        assert state['model'] == 'Samsung SSD 980 500GB'
        assert state['passed'] is True
        # NVMe defines percentage_used, so it needs no interpretation
        # and is flagged as exact.
        assert state['wear_pct'] == 3
        assert state['wear_is_exact'] is True
        assert state['power_on_hours'] == 4210
        assert state['temperature_c'] == 34
        assert state['pre_eol'] is None

    def test_nvme_spare_below_threshold_is_urgent(self) -> None:
        doc = json.loads(json.dumps(NVME_HEALTHY))
        doc['nvme_smart_health_information_log']['available_spare'] = 5
        doc['nvme_smart_health_information_log'][
            'available_spare_threshold'
        ] = 10

        # The drive has burned through the blocks it keeps to remap
        # failures. This is NVMe's spelling of eMMC's PRE_EOL urgent.
        assert smart.parse(doc, '/dev/nvme0n1')['pre_eol'] == 'urgent'

    def test_nvme_critical_warning_is_urgent(self) -> None:
        doc = json.loads(json.dumps(NVME_HEALTHY))
        doc['nvme_smart_health_information_log']['critical_warning'] = 4

        assert smart.parse(doc, '/dev/nvme0n1')['pre_eol'] == 'urgent'

    def test_ata_wear_is_inverted_and_marked_inexact(self) -> None:
        state = smart.parse(ATA_WORN, '/dev/sda')

        # ATA normalized values count DOWN from 100 as life is spent,
        # so 12 remaining means 88 used.
        assert state['wear_pct'] == 88
        # ...but that is a convention, not a spec, so it is advisory.
        assert state['wear_is_exact'] is False
        assert state['power_on_hours'] == 31000
        assert state['reallocated_sectors'] == 12
        assert state['pending_sectors'] == 3

    def test_remapped_sectors_warn(self) -> None:
        state = smart.parse(ATA_WORN, '/dev/sda')

        # Reallocated and pending counts are well defined across
        # vendors, unlike the wear attributes, so they carry the
        # verdict even while the drive still says it passed.
        assert state['pre_eol'] == 'warning'

    def test_failed_self_assessment_is_urgent(self) -> None:
        doc = json.loads(json.dumps(NVME_HEALTHY))
        doc['smart_status']['passed'] = False

        state = smart.parse(doc, '/dev/nvme0n1')

        # The drive's own prediction outranks everything else.
        assert state['passed'] is False
        assert state['pre_eol'] == 'urgent'

    def test_a_clean_ata_drive_does_not_warn(self) -> None:
        doc = json.loads(json.dumps(ATA_WORN))
        doc['ata_smart_attributes']['table'] = [
            {'id': 5, 'value': 100, 'raw': {'value': 0}},
            {'id': 197, 'value': 100, 'raw': {'value': 0}},
        ]

        assert smart.parse(doc, '/dev/sda')['pre_eol'] is None


class TestArgv:
    def test_elevates_when_not_root(self) -> None:
        # privileged: true on the container is not enough: the viewer
        # process drops to an unprivileged user, and /dev/sda is
        # root:disk besides.
        with mock.patch('os.geteuid', return_value=1000):
            argv = smart._argv('/dev/sda')

        assert argv[:2] == ['sudo', '-n']
        assert argv[2] == smart.SMARTCTL

    def test_calls_directly_when_root(self) -> None:
        with mock.patch('os.geteuid', return_value=0):
            argv = smart._argv('/dev/sda')

        assert argv[0] == smart.SMARTCTL

    def test_matches_the_sudoers_rule_in_the_viewer_image(self) -> None:
        """The sudoers grant is pinned to this exact argument list.

        sudo matches arguments literally, and a mismatch denies
        silently -- which looks exactly like a device with no SMART,
        so nothing would fail loudly. Reading the rule out of the
        Dockerfile template keeps the two from drifting apart.
        """
        template = pathlib.Path('docker/Dockerfile.viewer.j2').read_text()
        rules = [
            line
            for line in template.splitlines()
            if 'NOPASSWD:' in line and 'smartctl' in line
        ]
        assert len(rules) == 1, rules

        # The rule lives inside a quoted printf argument with a shell
        # line continuation, so peel both off before comparing.
        granted = rules[0].split('NOPASSWD:', 1)[1].strip()
        granted = granted.rstrip('\\').strip().rstrip('\'"').strip()
        with mock.patch('os.geteuid', return_value=1000):
            argv = smart._argv('/dev/sda')

        # Drop the sudo prefix; what sudo authorises is the rest.
        command = argv[2:]
        assert granted.split()[0] == command[0], (
            f'sudoers grants {granted.split()[0]!r} but _argv runs '
            f'{command[0]!r}'
        )
        # Flags must match exactly and in order; only the device is a
        # wildcard, because it is resolved at runtime.
        assert granted.split()[1:-1] == command[1:-1], (
            f'sudoers flags {granted.split()[1:-1]} != _argv flags '
            f'{command[1:-1]}'
        )
        assert granted.split()[-1] == '/dev/*'
        assert command[-1].startswith('/dev/')


class TestRunSmartctl:
    def test_missing_smartctl_is_none_not_a_crash(self) -> None:
        # smartmontools ships only on the boards that can have a
        # SMART-capable device, so absent is the normal case on most
        # of the fleet.
        with mock.patch(
            'subprocess.run', side_effect=FileNotFoundError('smartctl')
        ):
            assert smart.run_smartctl('/dev/sda') is None

    def test_timeout_is_none_not_a_hang(self) -> None:
        # An ATA command to a struggling drive can block; the sampler
        # thread must not wedge on it.
        with mock.patch(
            'subprocess.run',
            side_effect=subprocess.TimeoutExpired('smartctl', 30),
        ):
            assert smart.run_smartctl('/dev/sda') is None

    def test_unparseable_output_is_none(self) -> None:
        with mock.patch(
            'subprocess.run',
            return_value=mock.Mock(stdout='not json', returncode=0),
        ):
            assert smart.run_smartctl('/dev/sda') is None

    def test_nonzero_exit_still_returns_the_document(self) -> None:
        # "This device has no SMART" is an answer the caller needs,
        # and smartctl reports it with a nonzero exit.
        with mock.patch(
            'subprocess.run',
            return_value=mock.Mock(
                stdout=json.dumps(UNSUPPORTED), returncode=1
            ),
        ):
            doc = smart.run_smartctl('/dev/vda')

        assert doc is not None
        assert smart.parse(doc, '/dev/vda')['supported'] is False


@pytest.fixture
def fake_redis() -> Any:
    from conftest import _make_fake_redis

    return _make_fake_redis()


class TestPublishAndRead:
    def test_round_trip(self, fake_redis: Any) -> None:
        state = smart.parse(NVME_HEALTHY, '/dev/nvme0n1')
        smart.publish(fake_redis, state)

        assert smart.read(fake_redis) == state

    def test_published_with_a_ttl(self, fake_redis: Any) -> None:
        # The key expiring is how the server learns the viewer stopped
        # reporting, so a drive that has been unreadable for a day is
        # not quoted as current.
        smart.publish(fake_redis, smart.parse(NVME_HEALTHY, '/dev/nvme0n1'))

        assert fake_redis.set.call_args.kwargs['ex'] == smart.TTL_S

    def test_absent_fact_reads_as_none(self, fake_redis: Any) -> None:
        assert smart.read(fake_redis) is None

    def test_corrupt_fact_reads_as_none(self, fake_redis: Any) -> None:
        fake_redis.set(smart.REDIS_KEY, 'not json')

        assert smart.read(fake_redis) is None

    def test_publish_survives_a_redis_outage(self, fake_redis: Any) -> None:
        fake_redis.set.side_effect = RuntimeError('redis down')

        # Must not raise: this runs on the viewer's sampler thread.
        smart.publish(fake_redis, smart.parse(NVME_HEALTHY, '/dev/nvme0n1'))
