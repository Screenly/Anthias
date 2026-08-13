"""Tests for the scheduled display-power logic.

The schedule helpers are pure so the awkward cases — wrapping past
midnight, and which day an overnight period belongs to — are testable
without a clock, a worker, or CEC hardware.
"""

from datetime import UTC, datetime, time
from typing import Any
from unittest import mock

import pytest

from anthias_server.lib import cec_client, display_power


def _at(day: str, hhmm: str) -> datetime:
    """A datetime on a known weekday. 2026-08-10 is a Monday.

    The tzinfo is incidental — the caller passes an already-localised
    datetime (the task uses ``timezone.localtime()``), and only
    ``.weekday()`` and ``.time()`` are read.
    """
    days = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    hour, minute = (int(p) for p in hhmm.split(':'))
    return datetime(2026, 8, 10 + days.index(day), hour, minute, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('08:00', time(8, 0)),
        ('8:00', time(8, 0)),
        ('23:59', time(23, 59)),
        ('00:00', time(0, 0)),
        (' 09:30 ', time(9, 30)),
    ],
)
def test_parse_hhmm_accepts_valid_times(raw: str, expected: time) -> None:
    assert display_power.parse_hhmm(raw) == expected


@pytest.mark.parametrize(
    'raw', ['', None, 'nonsense', '25:00', '08:99', '08', 'aa:bb']
)
def test_parse_hhmm_rejects_junk_without_raising(raw: Any) -> None:
    """A malformed config value must degrade to 'no schedule', never
    crash the beat that reads it every minute."""
    assert display_power.parse_hhmm(raw) is None


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('0,1,2,3,4,5,6', {0, 1, 2, 3, 4, 5, 6}),
        ('0,4', {0, 4}),
        ('  1 , 3  ', {1, 3}),
        # Out-of-range and junk entries are dropped, not fatal.
        ('0,9,-1,abc,2', {0, 2}),
    ],
)
def test_parse_days(raw: str, expected: set[int]) -> None:
    assert display_power.parse_days(raw) == expected


@pytest.mark.parametrize('raw', ['', '   ', None, ',,,', '9,10'])
def test_parse_days_defaults_to_every_day(raw: Any) -> None:
    """Selecting no days would make an *enabled* schedule silently inert,
    which reads as a broken feature rather than a configuration choice."""
    assert display_power.parse_days(raw) == set(range(7))


# ---------------------------------------------------------------------------
# Same-day window
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('now', 'expected'),
    [
        ('07:59', False),
        ('08:00', True),
        ('12:00', True),
        ('17:59', True),
        # The off boundary is exclusive: at exactly the off time the
        # display is already off.
        ('18:00', False),
        ('23:00', False),
    ],
)
def test_same_day_window(now: str, expected: bool) -> None:
    assert (
        display_power.should_be_on(
            _at('mon', now), time(8, 0), time(18, 0), set(range(7))
        )
        is expected
    )


def test_same_day_window_respects_deselected_day() -> None:
    assert (
        display_power.should_be_on(
            _at('sun', '12:00'), time(8, 0), time(18, 0), {0, 1, 2, 3, 4}
        )
        is False
    )


# ---------------------------------------------------------------------------
# Overnight window — the case that is easy to get wrong
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('now', 'expected'),
    [
        ('17:59', False),
        ('18:00', True),
        ('23:59', True),
        ('00:30', True),
        ('05:59', True),
        ('06:00', False),
        ('12:00', False),
    ],
)
def test_overnight_window(now: str, expected: bool) -> None:
    assert (
        display_power.should_be_on(
            _at('mon', now), time(18, 0), time(6, 0), set(range(7))
        )
        is expected
    )


def test_overnight_small_hours_belong_to_the_previous_day() -> None:
    """An 18:00->06:00 period that starts on Friday runs into Saturday.
    Saturday's small hours are governed by *Friday's* checkbox — reading
    Saturday's would cut the display at midnight every Friday night."""
    weekdays = {0, 1, 2, 3, 4}  # Mon-Fri

    # Friday evening: on, because Friday is selected.
    assert (
        display_power.should_be_on(
            _at('fri', '20:00'), time(18, 0), time(6, 0), weekdays
        )
        is True
    )
    # Saturday 02:00 still belongs to Friday's period, so still on...
    assert (
        display_power.should_be_on(
            _at('sat', '02:00'), time(18, 0), time(6, 0), weekdays
        )
        is True
    )
    # ...but Saturday evening starts a new period on an unselected day.
    assert (
        display_power.should_be_on(
            _at('sat', '20:00'), time(18, 0), time(6, 0), weekdays
        )
        is False
    )
    # Sunday 02:00 would belong to Saturday's (unselected) period.
    assert (
        display_power.should_be_on(
            _at('sun', '02:00'), time(18, 0), time(6, 0), weekdays
        )
        is False
    )


# ---------------------------------------------------------------------------
# "No opinion" cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('on_time', 'off_time'),
    [
        (None, time(18, 0)),
        (time(8, 0), None),
        (None, None),
        # Identical times describe neither an on-period nor an
        # off-period; guessing either way would be wrong.
        (time(8, 0), time(8, 0)),
    ],
)
def test_unusable_schedule_expresses_no_opinion(
    on_time: time | None, off_time: time | None
) -> None:
    assert (
        display_power.should_be_on(
            _at('mon', '12:00'), on_time, off_time, set(range(7))
        )
        is None
    )


# ---------------------------------------------------------------------------
# Applying power — CEC first, local blanking as fallback
# ---------------------------------------------------------------------------


def test_apply_power_uses_cec_and_blanks_locally() -> None:
    """Both, not either. A device can have a CEC TV on one output and a
    plain monitor on another; CEC alone would report success and leave
    the monitor lit all night. A monitor with no CEC in its EDID is not
    even counted in `attempted`, so there is no count to detect it by."""
    with (
        mock.patch.object(cec_client, 'set_power', return_value=(2, 2)),
        mock.patch('anthias_server.settings.ViewerPublisher') as publisher,
    ):
        summary = display_power.apply_power(False)
    assert 'CEC' in summary
    assert 'local blanking' in summary
    publisher.get_instance.return_value.send_to_viewer.assert_called_once_with(
        'blank'
    )


def test_apply_power_falls_back_to_blanking_without_a_cec_peer() -> None:
    """The plain-monitor case — the majority of the fleet by the testbed
    measurements. Without this the schedule would do nothing at all."""
    with (
        mock.patch.object(cec_client, 'set_power', return_value=(0, 1)),
        mock.patch('anthias_server.settings.ViewerPublisher') as publisher,
    ):
        summary = display_power.apply_power(False)
    assert 'local blanking' in summary
    publisher.get_instance.return_value.send_to_viewer.assert_called_once_with(
        'blank'
    )


def test_apply_power_unblanks_when_turning_on() -> None:
    with (
        mock.patch.object(cec_client, 'set_power', return_value=(0, 0)),
        mock.patch('anthias_server.settings.ViewerPublisher') as publisher,
    ):
        display_power.apply_power(True)
    publisher.get_instance.return_value.send_to_viewer.assert_called_once_with(
        'unblank'
    )


def test_apply_power_blanks_before_cec_so_a_cec_failure_still_darkens() -> (
    None
):
    """The blank goes out first, so even a CEC failure that propagates
    leaves the screen in the right visual state. The error is *not*
    swallowed: the scheduler must retry rather than latch a command that
    never reached the display."""
    with (
        mock.patch.object(
            cec_client,
            'set_power',
            side_effect=cec_client.ViewerUnavailableError('no answer'),
        ),
        mock.patch('anthias_server.settings.ViewerPublisher') as publisher,
        pytest.raises(cec_client.ViewerUnavailableError),
    ):
        display_power.apply_power(False)
    publisher.get_instance.return_value.send_to_viewer.assert_called_once_with(
        'blank'
    )
