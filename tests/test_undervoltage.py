"""Tests for kernel-hwmon under-voltage detection and its latch."""

import json
from collections.abc import Iterator
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


@pytest.fixture(autouse=True)
def _clear_alarm_path_cache() -> Iterator[None]:
    """find_alarm_path() memoises, so reset it around every test.

    Without this the first test's monkeypatched HWMON_ROOT would be
    cached and every later test would assert against it.
    """
    undervoltage.reset_alarm_path_cache()
    yield
    undervoltage.reset_alarm_path_cache()


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

    def test_distinct_brown_outs_accumulate(
        self, fake_redis: MagicMock
    ) -> None:
        # Each dip has to recover before the next one counts as a
        # separate event.
        for _ in range(3):
            undervoltage.record_observation(fake_redis, True, boot_id='boot-a')
            state = undervoltage.record_observation(
                fake_redis, False, boot_id='boot-a'
            )

        assert state['count'] == 3
        assert state['first_seen'] <= state['last_seen']

    def test_sustained_brown_out_counts_once(
        self, fake_redis: MagicMock
    ) -> None:
        # The watcher re-samples on a 30s timeout as well as on kernel
        # events, and re-seeds on every worker start. Counting each
        # truthy reading would render "power dropped too low 21 times"
        # for one condition that never went away.
        for _ in range(20):
            state = undervoltage.record_observation(
                fake_redis, True, boot_id='boot-a'
            )

        assert state['count'] == 1
        assert state['active'] is True

    def test_worker_restart_does_not_inflate_the_count(
        self, fake_redis: MagicMock
    ) -> None:
        # start() seeds the latch from the live attribute on every
        # celery worker start; an ongoing brown-out must not be
        # recounted each time.
        undervoltage.record_observation(fake_redis, True, boot_id='boot-a')
        state = undervoltage.record_observation(
            fake_redis, True, boot_id='boot-a'
        )

        assert state['count'] == 1

    def test_corrupt_count_does_not_raise(self, fake_redis: MagicMock) -> None:
        # Valid JSON, unusable count. An unguarded int() here would
        # raise before the write that would overwrite the bad value,
        # leaving the feature permanently dead.
        fake_redis.set(
            undervoltage.REDIS_KEY,
            json.dumps({'boot_id': 'boot-a', 'count': 'abc'}),
        )

        state = undervoltage.record_observation(
            fake_redis, True, boot_id='boot-a'
        )

        assert state['count'] == 1

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

    def test_live_alarm_is_written_back_to_the_latch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_redis: MagicMock,
    ) -> None:
        # With the watcher down, a page render is the only thing that
        # observes the alarm. If that observation is not persisted the
        # banner disappears the moment power recovers, contradicting
        # the rule that seen_since_boot only goes up within a boot.
        root = _make_hwmon(tmp_path, {'hwmon0': ('rpi_volt', 1)})
        monkeypatch.setattr(undervoltage, 'HWMON_ROOT', root)
        monkeypatch.setattr(undervoltage, 'get_boot_id', lambda: 'boot-a')

        undervoltage.get_state(fake_redis)

        stored = json.loads(fake_redis.get(undervoltage.REDIS_KEY))
        assert stored['seen_since_boot'] is True
        assert stored['count'] == 1

    def test_warning_survives_recovery_after_a_render_only_sighting(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_redis: MagicMock,
    ) -> None:
        monkeypatch.setattr(undervoltage, 'get_boot_id', lambda: 'boot-a')

        # Render sees a live alarm...
        root = _make_hwmon(tmp_path, {'hwmon0': ('rpi_volt', 1)})
        monkeypatch.setattr(undervoltage, 'HWMON_ROOT', root)
        assert undervoltage.should_warn(undervoltage.get_state(fake_redis))

        # ...power recovers, and a later render must still warn.
        (tmp_path / 'hwmon' / 'hwmon0' / undervoltage.ALARM_ATTR).write_text(
            '0\n'
        )
        undervoltage.reset_alarm_path_cache()
        state = undervoltage.get_state(fake_redis)

        assert state['active'] is False
        assert state['seen_since_boot'] is True
        assert undervoltage.should_warn(state) is True

    def test_healthy_render_does_not_write(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_redis: MagicMock,
    ) -> None:
        # The banner is on every page, so the steady-state path stays
        # a pure read rather than a Redis write per render.
        root = _make_hwmon(tmp_path, {'hwmon0': ('rpi_volt', 0)})
        monkeypatch.setattr(undervoltage, 'HWMON_ROOT', root)

        undervoltage.get_state(fake_redis)

        assert fake_redis.set.call_count == 0


class TestAlarmPathCache:
    def test_scan_runs_once_across_repeated_lookups(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_hwmon(tmp_path, {'hwmon0': ('rpi_volt', 0)})
        monkeypatch.setattr(undervoltage, 'HWMON_ROOT', root)
        calls = []
        real_scan = undervoltage._scan_for_alarm_path

        def counting_scan() -> str | None:
            calls.append(1)
            return real_scan()

        monkeypatch.setattr(
            undervoltage, '_scan_for_alarm_path', counting_scan
        )

        for _ in range(5):
            undervoltage.find_alarm_path()

        assert len(calls) == 1

    def test_unsupported_result_is_cached_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A cached None must be distinguishable from "not looked up
        # yet", or an x86 box would rescan /sys on every page render.
        monkeypatch.setattr(undervoltage, 'HWMON_ROOT', str(tmp_path / 'no'))
        calls: list[int] = []

        def counting_miss() -> str | None:
            calls.append(1)
            return None

        monkeypatch.setattr(
            undervoltage, '_scan_for_alarm_path', counting_miss
        )

        assert undervoltage.find_alarm_path() is None
        assert undervoltage.find_alarm_path() is None
        assert len(calls) == 1

    def test_use_cache_false_forces_a_rescan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_hwmon(tmp_path, {'hwmon0': ('rpi_volt', 0)})
        monkeypatch.setattr(undervoltage, 'HWMON_ROOT', root)
        undervoltage.find_alarm_path()

        calls: list[int] = []

        def counting_miss() -> str | None:
            calls.append(1)
            return None

        monkeypatch.setattr(
            undervoltage, '_scan_for_alarm_path', counting_miss
        )
        undervoltage.find_alarm_path(use_cache=False)

        assert len(calls) == 1


class TestUnknownBootId:
    def test_latch_is_not_persisted_without_a_boot_id(
        self, fake_redis: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Nothing to key the reset on, so a latch written now could
        # outlive the boot it describes and warn forever.
        # NB: the ``boot_id=None`` argument means "resolve it", so the
        # unknown case has to come from get_boot_id() itself.
        monkeypatch.setattr(undervoltage, 'get_boot_id', lambda: None)

        state = undervoltage.record_observation(fake_redis, True)

        assert state['active'] is True
        assert fake_redis.get(undervoltage.REDIS_KEY) is None

    def test_stored_latch_is_discarded_without_a_boot_id(
        self, fake_redis: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The trap: a stored `boot_id: null` compares equal to a
        # current `None`, so the latch would be treated as current
        # forever and the warning would never reset.
        fake_redis.set(
            undervoltage.REDIS_KEY,
            json.dumps(
                {
                    'boot_id': None,
                    'active': True,
                    'seen_since_boot': True,
                    'count': 7,
                }
            ),
        )
        monkeypatch.setattr(undervoltage, 'get_boot_id', lambda: None)

        state = undervoltage.record_observation(fake_redis, False)

        assert state['seen_since_boot'] is False
        assert state['count'] == 0
