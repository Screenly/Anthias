"""Tests for kernel-hwmon under-voltage detection and its latch."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anthias_common import undervoltage


def _make_hwmon(
    tmp_path: Path,
    entries: dict[str, tuple[str, object]],
) -> str:
    """Build a fake ``/sys/class/hwmon`` tree.

    ``entries`` maps hwmon dir name → (sensor name, alarm value or
    ``None`` to omit the attribute entirely).
    """
    root = tmp_path / 'hwmon'
    root.mkdir()
    for dirname, (name, alarm) in entries.items():
        node = root / dirname
        node.mkdir()
        (node / 'name').write_text(f'{name}\n')
        if alarm is not None:
            (node / undervoltage.ALARM_ATTR).write_text(f'{alarm}\n')
    return str(root)


@pytest.fixture
def fake_redis() -> MagicMock:
    from conftest import _make_fake_redis

    return _make_fake_redis()


class TestFindAlarmPath:
    def test_finds_rpi_volt_among_other_sensors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Real boards expose several hwmon devices and the numbering
        # shifts with probe order, which is why we match on name.
        root = _make_hwmon(
            tmp_path,
            {
                'hwmon0': ('cpu_thermal', None),
                'hwmon1': ('rpi_volt', 0),
            },
        )
        monkeypatch.setattr(undervoltage, 'HWMON_ROOT', root)

        found = undervoltage.find_alarm_path()

        assert found is not None
        assert found.endswith('hwmon1/in0_lcrit_alarm')

    def test_returns_none_without_the_driver(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # x86 and most non-Pi arm64 SBCs. Must stay silent rather than
        # claim a healthy supply we can't actually verify.
        root = _make_hwmon(tmp_path, {'hwmon0': ('coretemp', None)})
        monkeypatch.setattr(undervoltage, 'HWMON_ROOT', root)

        assert undervoltage.find_alarm_path() is None

    def test_returns_none_when_hwmon_is_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(undervoltage, 'HWMON_ROOT', str(tmp_path / 'nope'))

        assert undervoltage.find_alarm_path() is None

    def test_ignores_rpi_volt_without_the_attribute(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_hwmon(tmp_path, {'hwmon0': ('rpi_volt', None)})
        monkeypatch.setattr(undervoltage, 'HWMON_ROOT', root)

        assert undervoltage.find_alarm_path() is None


class TestReadAlarm:
    @pytest.mark.parametrize(
        'raw,expected', [('0', False), ('1', True), ('0\n', False)]
    )
    def test_reads_the_boolean_attribute(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        raw: str,
        expected: bool,
    ) -> None:
        root = _make_hwmon(tmp_path, {'hwmon0': ('rpi_volt', raw.strip())})
        monkeypatch.setattr(undervoltage, 'HWMON_ROOT', root)

        assert undervoltage.read_alarm() is expected

    def test_unsupported_reads_none_not_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # None ("unknown") and False ("fine") must stay distinct. The
        # UI only warns on a positive reading, and only claims health
        # when it actually has one.
        monkeypatch.setattr(undervoltage, 'HWMON_ROOT', str(tmp_path / 'nope'))

        assert undervoltage.read_alarm() is None

    def test_unreadable_attribute_is_unknown(self, tmp_path: Path) -> None:
        # An attribute that vanished between lookup and read must not
        # report False and silently clear a live warning.
        assert undervoltage.read_alarm(str(tmp_path / 'gone')) is None


class TestRecordObservation:
    def test_first_alarm_sets_the_latch(self, fake_redis: MagicMock) -> None:
        state = undervoltage.record_observation(
            fake_redis, True, boot_id='boot-a'
        )

        assert state['active'] is True
        assert state['seen_since_boot'] is True
        assert state['count'] == 1
        assert state['first_seen'] is not None
        assert state['last_seen'] == state['first_seen']

    def test_recovery_clears_active_but_not_history(
        self, fake_redis: MagicMock
    ) -> None:
        undervoltage.record_observation(fake_redis, True, boot_id='boot-a')
        state = undervoltage.record_observation(
            fake_redis, False, boot_id='boot-a'
        )

        # A supply that dipped and recovered is still a failing supply;
        # the banner de-escalates to amber rather than disappearing.
        assert state['active'] is False
        assert state['seen_since_boot'] is True
        assert state['count'] == 1

    def test_repeat_alarms_accumulate(self, fake_redis: MagicMock) -> None:
        for _ in range(3):
            state = undervoltage.record_observation(
                fake_redis, True, boot_id='boot-a'
            )

        assert state['count'] == 3
        assert state['first_seen'] <= state['last_seen']

    def test_reboot_resets_the_latch(self, fake_redis: MagicMock) -> None:
        undervoltage.record_observation(fake_redis, True, boot_id='boot-a')

        # Redis persists to a volume, so without the boot-id check a
        # device that browned out last week would still be warning
        # after a reboot and a new power supply.
        state = undervoltage.record_observation(
            fake_redis, False, boot_id='boot-b'
        )

        assert state['seen_since_boot'] is False
        assert state['count'] == 0

    def test_same_boot_survives_a_reread(self, fake_redis: MagicMock) -> None:
        undervoltage.record_observation(fake_redis, True, boot_id='boot-a')
        state = undervoltage.record_observation(
            fake_redis, False, boot_id='boot-a'
        )

        assert state['seen_since_boot'] is True

    def test_corrupt_latch_is_discarded(self, fake_redis: MagicMock) -> None:
        fake_redis.set(undervoltage.REDIS_KEY, 'not json')

        state = undervoltage.record_observation(
            fake_redis, False, boot_id='boot-a'
        )

        assert state['seen_since_boot'] is False

    def test_boot_id_is_persisted(self, fake_redis: MagicMock) -> None:
        undervoltage.record_observation(fake_redis, True, boot_id='boot-a')

        stored = json.loads(fake_redis.get(undervoltage.REDIS_KEY))
        assert stored['boot_id'] == 'boot-a'
        # 'supported' is derived at read time from the sensor's
        # presence, so persisting it would let a stale value outlive
        # the hardware it described.
        assert 'supported' not in stored

    def test_redis_failure_still_returns_the_reading(
        self, fake_redis: MagicMock
    ) -> None:
        fake_redis.set.side_effect = RuntimeError('redis down')

        state = undervoltage.record_observation(
            fake_redis, True, boot_id='boot-a'
        )

        assert state['active'] is True


class TestGetState:
    def test_unsupported_board(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_redis: MagicMock,
    ) -> None:
        monkeypatch.setattr(undervoltage, 'HWMON_ROOT', str(tmp_path / 'nope'))

        state = undervoltage.get_state(fake_redis)

        assert state['supported'] is False
        assert undervoltage.should_warn(state) is False

    def test_healthy_board_does_not_warn(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_redis: MagicMock,
    ) -> None:
        root = _make_hwmon(tmp_path, {'hwmon0': ('rpi_volt', 0)})
        monkeypatch.setattr(undervoltage, 'HWMON_ROOT', root)

        state = undervoltage.get_state(fake_redis)

        assert state['supported'] is True
        assert state['active'] is False
        assert undervoltage.should_warn(state) is False

    def test_live_alarm_warns_without_a_latch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_redis: MagicMock,
    ) -> None:
        # Covers the watcher thread being dead: a page render must
        # still report a live alarm it reads for itself.
        root = _make_hwmon(tmp_path, {'hwmon0': ('rpi_volt', 1)})
        monkeypatch.setattr(undervoltage, 'HWMON_ROOT', root)

        state = undervoltage.get_state(fake_redis)

        assert state['active'] is True
        assert state['seen_since_boot'] is True
        assert undervoltage.should_warn(state) is True

    def test_history_warns_after_recovery(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_redis: MagicMock,
    ) -> None:
        root = _make_hwmon(tmp_path, {'hwmon0': ('rpi_volt', 0)})
        monkeypatch.setattr(undervoltage, 'HWMON_ROOT', root)
        monkeypatch.setattr(undervoltage, 'get_boot_id', lambda: 'boot-a')
        undervoltage.record_observation(fake_redis, True, boot_id='boot-a')

        state = undervoltage.get_state(fake_redis)

        assert state['active'] is False
        assert state['seen_since_boot'] is True
        assert undervoltage.should_warn(state) is True
