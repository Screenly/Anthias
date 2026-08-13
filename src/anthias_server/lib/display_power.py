"""Scheduled display power — turn the screen off out of hours.

Two layers, because CEC alone only works for the subset of installs that
have a CEC-capable TV attached:

  * **CEC** — genuinely powers the display down, and is what an
    operator wants on a real TV. Executed by the viewer container (see
    ``lib/cec_client``), which is the only one that can reach
    ``/dev/cec*`` on every board and every deployment.
  * **Local blanking** — the viewer's ``blank`` / ``unblank`` commands
    added in #3065 — is the fallback. On wayland boards that is a real
    DPMS off via ``wlr-randr``; on eglfs/linuxfb the viewer paints the
    screen black, because the Qt app holds DRM master and an external
    blank is rejected.

Without the fallback the feature would do nothing on the large share of
the fleet running plain monitors, which is exactly the population the
testbed measurements showed cannot answer CEC at all.

The schedule itself is deliberately pure and separated from the Celery
task so the wrap-around-midnight cases are unit-testable without a
worker, a clock, or hardware.
"""

import logging
from datetime import datetime, time

from anthias_server.lib import cec_client

logger = logging.getLogger(__name__)

#: ``settings`` stores the selected weekdays as a comma-separated list
#: of Python weekday numbers (Monday=0 ... Sunday=6).
ALL_DAYS = '0,1,2,3,4,5,6'


def parse_hhmm(value: str | None) -> time | None:
    """Parse an ``HH:MM`` string, returning ``None`` if unusable.

    Returns ``None`` rather than raising because this reads operator
    input from a config file that may predate the field entirely; a
    malformed value must degrade to "no schedule", never crash the beat.
    """
    if not value:
        return None
    # ``HH:MM:SS`` is accepted because an <input type="time"> with any
    # sub-minute ``step`` posts seconds, and silently rejecting that
    # would discard a perfectly valid edit.
    parts = str(value).strip().split(':')
    if len(parts) not in (2, 3):
        logger.warning('Ignoring malformed display-power time %r', value)
        return None
    try:
        # Every component is parsed and range-checked, including the
        # seconds we are about to discard. Dropping it unread would let
        # '07:30:xx' through as 07:30, and the v2 serializer promises a
        # 400 for a malformed time rather than a silent reinterpretation
        # (Copilot).
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
        parsed = time(hour, minute, second)
    except (TypeError, ValueError):
        logger.warning('Ignoring malformed display-power time %r', value)
        return None
    # The schedule has minute resolution; seconds are validated above and
    # then dropped so the stored value round-trips as 'HH:MM'.
    return parsed.replace(second=0)


def parse_days(value: str | None) -> set[int]:
    """Parse the comma-separated weekday list into a set.

    An empty or unparsable value means "every day" — the schedule's
    on/off times are the meaningful part, and silently selecting *no*
    days would make an enabled schedule do nothing at all.
    """
    if value is None or not str(value).strip():
        return set(range(7))
    days = set()
    for token in str(value).split(','):
        token = token.strip()
        if not token:
            continue
        try:
            day = int(token)
        except ValueError:
            continue
        if 0 <= day <= 6:
            days.add(day)
    return days or set(range(7))


def should_be_on(
    now: datetime,
    on_time: time | None,
    off_time: time | None,
    days: set[int],
) -> bool | None:
    """Whether the display should be powered on at ``now``.

    ``None`` means "no opinion, leave the display alone" — returned when
    the schedule is not usable (a missing time, or identical on/off
    times, which describes neither an on-period nor an off-period).

    ``days`` selects the weekdays on which an on-period *begins*. That
    distinction matters for a schedule that wraps past midnight (on
    18:00, off 06:00): the small hours of Tuesday belong to Monday's
    on-period, so they are governed by Monday's checkbox, not Tuesday's.
    Getting this wrong would cut the display at midnight every night a
    following day was deselected.
    """
    if on_time is None or off_time is None or on_time == off_time:
        return None

    today = now.weekday()
    yesterday = (today - 1) % 7
    current = now.time()

    if on_time < off_time:
        # Ordinary same-day window, e.g. 08:00 -> 18:00.
        return today in days and on_time <= current < off_time

    # Wraps past midnight, e.g. 18:00 -> 06:00.
    if current >= on_time:
        return today in days
    if current < off_time:
        return yesterday in days
    return False


def apply_power(on: bool) -> str:
    """Drive every display to ``on``, returning a human-readable summary.

    Sends the CEC command *and* the viewer's local blank/unblank, rather
    than treating blanking as a fallback used only when CEC finds
    nothing.

    Doing both is what makes the multi-display case correct. A device
    can have a CEC-capable TV on one output and a plain monitor on
    another; CEC would power down the TV and report success, leaving the
    monitor lit all night. Worse, a monitor whose EDID advertises no CEC
    has no physical address at all, so it is not even counted among the
    adapters we attempted — there is no number we could compare against
    to detect that it was missed.

    The overlap is harmless: blanking a display that CEC already powered
    off changes nothing, and on the way back up the display is both
    powered on and unblanked.
    """
    # Imported here rather than at module scope: settings pulls in the
    # Redis connection machinery, and this module is imported by the
    # unit tests for its pure schedule helpers.
    from anthias_server.settings import ViewerPublisher

    # The local blank/unblank goes first, and unconditionally — blank on
    # the way down, unblank on the way up. It is the layer that works
    # everywhere, and doing it before CEC means a contended bus (below)
    # still leaves the screen in the right visual state.
    ViewerPublisher.get_instance().send_to_viewer('unblank' if on else 'blank')

    # Nothing transmitted means the caller must not record the command
    # as delivered — a scheduler that latched here would leave the TV
    # powered on for the rest of the off-period. So this propagates
    # rather than being swallowed.
    acknowledged, attempted = cec_client.set_power(on)

    if acknowledged:
        return (
            f'CEC ({acknowledged}/{attempted} display(s) acknowledged) '
            f'+ local blanking'
        )
    detail = 'no CEC display acknowledged' if attempted else 'no CEC link'
    return f'local blanking ({detail})'
