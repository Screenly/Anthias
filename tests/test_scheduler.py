import logging
import os
from datetime import datetime, time, timedelta
from typing import Any

import time_machine
from django.test import TestCase
from django.utils import timezone

from anthias_app.models import Asset
from settings import settings
from viewer.scheduling import (
    Scheduler,
    WINDOWED_DEADLINE_CAP_SECONDS,
    generate_asset_list,
)

logging.disable(logging.CRITICAL)


_DEFAULT_PLAY_DAYS = '[1, 2, 3, 4, 5, 6, 7]'

ASSET_X = {
    'mimetype': 'web',
    'asset_id': '4c8dbce552edb5812d3a866cfe5f159d',
    'name': 'WireLoad',
    'uri': 'http://www.wireload.net',
    'start_date': timezone.now() - timedelta(days=3),
    'end_date': timezone.now() + timedelta(days=3),
    'duration': 5,
    'is_enabled': 1,
    'nocache': 0,
    'is_processing': 0,
    'play_order': 1,
    'skip_asset_check': 0,
    'play_days': _DEFAULT_PLAY_DAYS,
    'play_time_from': None,
    'play_time_to': None,
}

ASSET_X_DIFF = {'duration': 10}

ASSET_Y = {
    'mimetype': 'image',
    'asset_id': '7e978f8c1204a6f70770a1eb54a76e9b',
    'name': 'Google',
    'uri': 'https://www.google.com/images/srpr/logo3w.png',
    'start_date': timezone.now() - timedelta(days=1),
    'end_date': timezone.now() + timedelta(days=2),
    'duration': 6,
    'is_enabled': 1,
    'nocache': 0,
    'is_processing': 0,
    'play_order': 0,
    'skip_asset_check': 0,
    'play_days': _DEFAULT_PLAY_DAYS,
    'play_time_from': None,
    'play_time_to': None,
}

ASSET_Z = {
    'mimetype': 'image',
    'asset_id': '7e978f8c1204a6f70770a1eb54a76e9c',
    'name': 'Google',
    'uri': 'https://www.google.com/images/srpr/logo3w.png',
    'start_date': timezone.now() - timedelta(days=1),
    'end_date': timezone.now() + timedelta(days=1),
    'duration': 6,
    'is_enabled': 1,
    'nocache': 0,
    'is_processing': 0,
    'play_order': 2,
    'skip_asset_check': 0,
    'play_days': _DEFAULT_PLAY_DAYS,
    'play_time_from': None,
    'play_time_to': None,
}

ASSET_TOMORROW = {
    'mimetype': 'image',
    'asset_id': '7e978f8c1204a6f70770a1eb54a76e9c',
    'name': 'Google',
    'uri': 'https://www.google.com/images/srpr/logo3w.png',
    'start_date': timezone.now() + timedelta(days=1),
    'end_date': timezone.now() + timedelta(days=1),
    'duration': 6,
    'is_enabled': 1,
    'nocache': 0,
    'is_processing': 0,
    'play_order': 2,
    'skip_asset_check': 0,
    'play_days': _DEFAULT_PLAY_DAYS,
    'play_time_from': None,
    'play_time_to': None,
}

FAKE_DB_PATH = '/tmp/fakedb'


class SchedulerTest(TestCase):
    def tearDown(self) -> None:
        settings['shuffle_playlist'] = False

    def create_assets(self, assets: list[dict[str, Any]]) -> None:
        for asset in assets:
            Asset.objects.create(**asset)

    def test_generate_asset_list_assets_should_return_list_sorted_by_play_order(
        self,
    ) -> None:  # noqa: E501
        self.create_assets([ASSET_X, ASSET_Y])
        assets, _ = generate_asset_list()
        self.assertEqual(assets, [ASSET_Y, ASSET_X])

    def test_generate_asset_list_check_deadline_if_both_active(self) -> None:
        self.create_assets([ASSET_X, ASSET_Y])
        _, deadline = generate_asset_list()
        self.assertEqual(deadline, ASSET_Y['end_date'])

    def test_generate_asset_list_check_deadline_if_asset_scheduled(
        self,
    ) -> None:
        """If ASSET_X is active and ASSET_X[end_date] == (now + 3) and
        ASSET_TOMORROW will be active tomorrow then deadline should be
        ASSET_TOMORROW[start_date]
        """
        self.create_assets([ASSET_X, ASSET_TOMORROW])
        _, deadline = generate_asset_list()
        self.assertEqual(deadline, ASSET_TOMORROW['start_date'])

    def test_get_next_asset_should_be_y_and_x(self) -> None:
        self.create_assets([ASSET_X, ASSET_Y])
        scheduler = Scheduler()

        expected_y = scheduler.get_next_asset()
        expected_x = scheduler.get_next_asset()

        self.assertEqual([expected_y, expected_x], [ASSET_Y, ASSET_X])

    def test_keep_same_position_on_playlist_update(self) -> None:
        self.create_assets([ASSET_X, ASSET_Y])
        scheduler = Scheduler()
        scheduler.get_next_asset()

        self.create_assets([ASSET_Z])
        scheduler.update_playlist()

        self.assertEqual(scheduler.index, 1)

    def test_counter_should_increment_after_full_asset_loop(self) -> None:
        settings['shuffle_playlist'] = True
        self.create_assets([ASSET_X, ASSET_Y])
        scheduler = Scheduler()

        self.assertEqual(scheduler.counter, 0)

        scheduler.get_next_asset()
        scheduler.get_next_asset()

        self.assertEqual(scheduler.counter, 1)

    def test_check_get_db_mtime(self) -> None:
        settings['database'] = FAKE_DB_PATH
        with open(FAKE_DB_PATH, 'a'):
            os.utime(FAKE_DB_PATH, (0, 0))

        self.assertEqual(0, Scheduler().get_db_mtime())

    def test_playlist_should_be_updated_after_deadline_reached(self) -> None:
        self.create_assets([ASSET_X, ASSET_Y])
        _, deadline = generate_asset_list()
        assert deadline is not None

        traveller = time_machine.travel(deadline + timedelta(seconds=1))
        traveller.start()

        scheduler = Scheduler()
        scheduler.refresh_playlist()

        self.assertEqual([ASSET_X], scheduler.assets)
        traveller.stop()


def _aware(
    year: int, month: int, day: int, hour: int, minute: int = 0
) -> datetime:
    """Build a timezone-aware datetime in the local zone."""
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=timezone.get_current_timezone(),
    )


def _scheduled_asset(
    asset_id: str = 'abc123', **overrides: Any
) -> dict[str, Any]:
    """Asset payload with date range covering the test reference era
    (so time_machine.travel into 2026 doesn't fall outside it)."""
    base = dict(ASSET_X)
    base['asset_id'] = asset_id
    base['name'] = asset_id
    base['start_date'] = _aware(2025, 1, 1, 0, 0)
    base['end_date'] = _aware(2027, 1, 1, 0, 0)
    base.update(overrides)
    return base


def _first_asset() -> Asset:
    """Asset.objects.first() narrowed for typed test assertions."""
    asset = Asset.objects.first()
    assert asset is not None
    return asset


class WindowFilterTest(TestCase):
    """is_active() with the new play_days / play_time_from / play_time_to."""

    def test_play_days_restricts_weekday(self) -> None:
        # 2026-01-05 is a Monday.
        Asset.objects.create(
            **_scheduled_asset(play_days='[1]'),
        )
        with time_machine.travel(_aware(2026, 1, 5, 12, 0)):
            self.assertTrue(_first_asset().is_active())
        with time_machine.travel(_aware(2026, 1, 6, 12, 0)):
            self.assertFalse(_first_asset().is_active())

    def test_play_time_window_restricts_hour(self) -> None:
        Asset.objects.create(
            **_scheduled_asset(
                play_time_from=time(9, 0),
                play_time_to=time(17, 0),
            ),
        )
        with time_machine.travel(_aware(2026, 1, 5, 10, 0)):
            self.assertTrue(_first_asset().is_active())
        with time_machine.travel(_aware(2026, 1, 5, 6, 0)):
            self.assertFalse(_first_asset().is_active())
        with time_machine.travel(_aware(2026, 1, 5, 17, 0)):
            self.assertFalse(_first_asset().is_active())

    def test_overnight_window_active_before_and_after_midnight(self) -> None:
        Asset.objects.create(
            **_scheduled_asset(
                play_days='[1]',
                play_time_from=time(22, 0),
                play_time_to=time(6, 0),
            ),
        )
        # Mon 23:30 — pre-midnight portion.
        with time_machine.travel(_aware(2026, 1, 5, 23, 30)):
            self.assertTrue(_first_asset().is_active())
        # Tue 02:30 — post-midnight, "yesterday" was Mon (in days).
        with time_machine.travel(_aware(2026, 1, 6, 2, 30)):
            self.assertTrue(_first_asset().is_active())
        # Tue 23:30 — pre-midnight, today is Tue (not in days).
        with time_machine.travel(_aware(2026, 1, 6, 23, 30)):
            self.assertFalse(_first_asset().is_active())
        # Wed 02:30 — post-midnight, "yesterday" was Tue (not in days).
        with time_machine.travel(_aware(2026, 1, 7, 2, 30)):
            self.assertFalse(_first_asset().is_active())

    def test_unscheduled_asset_unchanged(self) -> None:
        """Backward compat: defaults must not narrow is_active().

        ASSET_X carries no play_days/play_time_* overrides, so the new
        window filter should be a no-op and the existing date-range
        check should continue to govern is_active().
        """
        Asset.objects.create(**ASSET_X)
        self.assertTrue(_first_asset().is_active())


class WindowedDeadlineCapTest(TestCase):
    """Windowed assets cap the deadline so transitions are picked up."""

    def test_cap_applies_when_any_asset_has_window(self) -> None:
        # An asset with a play_days filter should cap the deadline at
        # ~ now + WINDOWED_DEADLINE_CAP_SECONDS, even if its date
        # boundaries are far out.
        Asset.objects.create(
            **_scheduled_asset(play_days='[1]'),
        )
        with time_machine.travel(_aware(2026, 1, 5, 12, 0)):
            now = timezone.now()
            _, deadline = generate_asset_list()
            assert deadline is not None
            cap = now + timedelta(seconds=WINDOWED_DEADLINE_CAP_SECONDS)
            # Allow tiny clock drift; deadline should be at the cap.
            self.assertLessEqual(
                abs((deadline - cap).total_seconds()),
                1,
            )

    def test_cap_does_not_apply_without_window(self) -> None:
        Asset.objects.create(**ASSET_X)
        _, deadline = generate_asset_list()
        # No windowing: deadline should be the asset's end_date.
        self.assertEqual(deadline, ASSET_X['end_date'])

    def test_deadline_never_lands_in_the_past(self) -> None:
        """A windowed-but-currently-inactive asset must not poison the
        deadline with its long-past start_date — that would cause
        refresh_playlist() to fire on every tick.
        """
        # Tue (day 2) at noon. Asset is restricted to Mon only.
        Asset.objects.create(
            **_scheduled_asset(play_days='[1]'),
        )
        with time_machine.travel(_aware(2026, 1, 6, 12, 0)):
            now = timezone.now()
            _, deadline = generate_asset_list()
        assert deadline is not None
        self.assertGreater(deadline, now)


class GetPlayDaysFallbackTest(TestCase):
    """get_play_days() must defend against junk in the column."""

    def test_malformed_json_falls_back_to_all_days(self) -> None:
        a = Asset.objects.create(**_scheduled_asset(play_days='not json'))
        self.assertEqual(a.get_play_days(), [1, 2, 3, 4, 5, 6, 7])

    def test_non_list_json_falls_back_to_all_days(self) -> None:
        a = Asset.objects.create(**_scheduled_asset(play_days='{"a": 1}'))
        self.assertEqual(a.get_play_days(), [1, 2, 3, 4, 5, 6, 7])

    def test_out_of_range_int_falls_back_to_all_days(self) -> None:
        a = Asset.objects.create(**_scheduled_asset(play_days='[0, 8]'))
        self.assertEqual(a.get_play_days(), [1, 2, 3, 4, 5, 6, 7])

    def test_non_int_element_falls_back_to_all_days(self) -> None:
        a = Asset.objects.create(**_scheduled_asset(play_days='["mon"]'))
        self.assertEqual(a.get_play_days(), [1, 2, 3, 4, 5, 6, 7])
