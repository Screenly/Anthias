"""Template filters for the asset list page.

`to_json` serialises an Asset (or any model instance) to a JSON string
safe for inlining into an Alpine `@click` handler. The form-modal opens
edit mode by reading these inline blobs rather than refetching from the
API — keeps the row markup self-contained.

`asset_date` formats an Asset's start/end timestamps using the
device-settings configured `date_format` and `use_24_hour_clock`
toggles so the table matches what the Settings page advertises
(matches the React component's Intl.DateTimeFormat output).
"""

import json
from datetime import date, datetime, time, timedelta
from typing import Any

from django.template import Library
from django.utils.safestring import SafeString, mark_safe
from django.utils import timezone

from anthias_server.settings import settings

register = Library()


# Maps the user-facing `date_format` strings the Settings page exposes
# (mm/dd/yyyy, etc.) to the strftime tokens we actually format with.
# The trailing time portion is appended dynamically based on the
# use_24_hour_clock toggle.
_DATE_FORMAT_MAP = {
    'mm/dd/yyyy': '%m/%d/%Y',
    'dd/mm/yyyy': '%d/%m/%Y',
    'yyyy/mm/dd': '%Y/%m/%d',
    'mm-dd-yyyy': '%m-%d-%Y',
    'dd-mm-yyyy': '%d-%m-%Y',
    'yyyy-mm-dd': '%Y-%m-%d',
    'mm.dd.yyyy': '%m.%d.%Y',
    'dd.mm.yyyy': '%d.%m.%Y',
    'yyyy.mm.dd': '%Y.%m.%d',
}


_DAY_LABELS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


@register.filter
def schedule_pills(asset: Any) -> list[dict[str, str]]:
    """Return the schedule window as a list of pill descriptors.

    Each pill is a {kind, label} dict the row template iterates over.
    `kind` is one of:
      - 'all'  — shorthand "Everyday" pill, emitted only when no
                 day-of-week filter narrows the schedule.
      - 'day'  — one per active weekday: 'Mon', 'Tue', ...
      - 'time' — the play_time_from/to window, formatted in the
                 device's configured 24h/12h clock.

    The list collapses to a single 'all' pill when the asset has no
    day filter and no time window — the row then renders a green-ish
    chip rather than a wall of seven Mon/Tue/Wed pills.
    """
    pills: list[dict[str, str]] = []
    days_set: list[int] = []
    if hasattr(asset, 'get_play_days'):
        days_set = asset.get_play_days()
    full_week = days_set == list(range(1, 8))

    pf = getattr(asset, 'play_time_from', None)
    pt = getattr(asset, 'play_time_to', None)

    if not full_week and days_set:
        for d in days_set:
            if 1 <= d <= 7:
                pills.append({'kind': 'day', 'label': _DAY_LABELS[d - 1]})
    elif not (pf and pt):
        # Full-week plays that also play all hours get the catch-all
        # "Everyday" pill so the row reads "Everyday" instead of
        # nothing — matches the tooltip we used to show.
        pills.append({'kind': 'all', 'label': 'Everyday'})

    if pf and pt:
        fmt = '%H:%M' if settings['use_24_hour_clock'] else '%I:%M %p'
        pills.append(
            {
                'kind': 'time',
                'label': (
                    f'{pf.strftime(fmt).lstrip("0")} – '
                    f'{pt.strftime(fmt).lstrip("0")}'
                ),
            }
        )
    return pills


@register.filter
def schedule_label(asset: Any) -> str:
    """Backwards-compat string version of schedule_pills.

    Kept so anything still calling the prior single-chip filter (or a
    test asserting on the join) keeps working. Joins the pill labels
    with ', ' for days and ' · ' between days and time.
    """
    pills = schedule_pills(asset)
    days = ', '.join(p['label'] for p in pills if p['kind'] == 'day')
    time_part = next((p['label'] for p in pills if p['kind'] == 'time'), '')
    if days and time_part:
        return f'{days} · {time_part}'
    return days or time_part


@register.filter
def asset_date(value: datetime | None) -> str:
    """Format an Asset start/end datetime using the configured
    date_format + use_24_hour_clock device settings.

    No `settings.load()` call: the AnthiasSettings singleton is loaded
    at django.setup() time and the only writer is the Settings page
    POST handler (which calls .save() against the same in-memory
    object). Re-reading the .conf file on every cell of the asset
    table — and again every 5 s on the HTMX poll — is real perf
    overhead on long playlists, so read the cached values directly."""
    if value is None:
        return ''
    date_part = _DATE_FORMAT_MAP.get(settings['date_format'], '%m/%d/%Y')
    time_part = '%H:%M:%S' if settings['use_24_hour_clock'] else '%I:%M:%S %p'
    local = timezone.localtime(value)
    return local.strftime(f'{date_part} {time_part}')


def _to_dict(obj: Any) -> Any:
    if hasattr(obj, '_meta'):
        out: dict[str, Any] = {}
        for field in obj._meta.fields:
            value = getattr(obj, field.name)
            out[field.name] = _coerce(value)
        # Pre-format the local-time strings the edit modal binds to so
        # the template doesn't re-do the timezone math in JS — keeps
        # parity with the React EditAssetModal which used Intl.
        if hasattr(obj, 'start_date') and obj.start_date:
            out['start_date_local'] = timezone.localtime(
                obj.start_date
            ).strftime('%Y-%m-%dT%H:%M')
        if hasattr(obj, 'end_date') and obj.end_date:
            out['end_date_local'] = timezone.localtime(obj.end_date).strftime(
                '%Y-%m-%dT%H:%M'
            )
        # Normalise play_days to a list[int] so the day-of-week
        # checkboxes can `.includes(day)` straight off Alpine state.
        # The TextField stores JSON; get_play_days() handles the parse
        # + clamp to 1-7.
        if hasattr(obj, 'get_play_days'):
            out['play_days_list'] = obj.get_play_days()
        # play_time_from / play_time_to are TimeFields that serialise
        # as ISO strings (HH:MM:SS); the <input type="time"> binding
        # wants HH:MM. Trim if present.
        for key in ('play_time_from', 'play_time_to'):
            v = out.get(key)
            if isinstance(v, str) and len(v) >= 5:
                out[key] = v[:5]
        return out
    return _coerce(obj)


def _coerce(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


@register.filter
def to_json(value: Any) -> SafeString:
    """Render an Asset (or any model) as a JSON literal for Alpine."""
    encoded = json.dumps(
        _to_dict(value),
        default=str,
        separators=(',', ':'),
    )
    # mark_safe lets `'` survive HTML autoescaping; we still hex-encode
    # the apostrophe and ampersand below to keep the literal valid as
    # a JS string inside an `x-on:click="openEdit(...)"` attribute.
    safe = (
        encoded.replace('&', '\\u0026')
        .replace("'", '\\u0027')
        .replace('<', '\\u003c')
        .replace('>', '\\u003e')
    )
    return mark_safe(safe)


@register.filter
def schedule_window(asset: Any) -> dict[str, str]:
    """Return a structured descriptor for the asset's start/end window.

    Renders as a single visual block in the schedule table — primary
    line is a relative phrase ('Active', 'Starts in 3 days', 'Ended 2
    days ago'), secondary is a compact absolute range ('Mar 12 → May
    23'). `kind` ∈ {'live', 'upcoming', 'expired', 'unknown'} so the
    template can colour-code without re-deriving the state.
    """
    start = getattr(asset, 'start_date', None)
    end = getattr(asset, 'end_date', None)
    if not start or not end:
        return {'kind': 'unknown', 'primary': 'No window', 'secondary': ''}

    from django.contrib.humanize.templatetags.humanize import naturalday

    now = timezone.now()
    start_local = timezone.localtime(start)
    end_local = timezone.localtime(end)

    # Django's naturalday humanises dates within a few days of today
    # ('today', 'tomorrow', 'yesterday') and falls back to the
    # locale-formatted absolute date otherwise. Drop the year suffix
    # when both endpoints land in the current calendar year. Title-case
    # the leading token so "today → May 5" reads as "Today → May 5"
    # (matches the primary line's sentence-case style).
    same_year = start_local.year == end_local.year == now.year
    abs_fmt = 'M j' if same_year else 'M j, Y'

    def _label(value: datetime) -> str:
        rendered = str(naturalday(value, abs_fmt))
        return rendered[:1].upper() + rendered[1:] if rendered else rendered

    secondary = f'{_label(start_local)} → {_label(end_local)}'

    # Disabled rows aren't playing, regardless of where 'now' falls
    # in the window — surface that explicitly so the operator doesn't
    # see "Live · ends in 21 days" on a paused asset.
    if not getattr(asset, 'is_enabled', True):
        return {
            'kind': 'disabled',
            'primary': 'Disabled',
            'secondary': secondary,
        }

    if now < start:
        delta = start - now
        primary = (
            'Starts soon'
            if delta.total_seconds() < 60 * 60
            else f'Starts {_relative_phrase(delta, future=True)}'
        )
        return {'kind': 'upcoming', 'primary': primary, 'secondary': secondary}
    if now > end:
        delta = now - end
        primary = (
            'Just ended'
            if delta.total_seconds() < 60 * 60
            else f'Ended {_relative_phrase(delta, future=False)}'
        )
        return {'kind': 'expired', 'primary': primary, 'secondary': secondary}

    # Inside the date window — but is the asset *actually playing*
    # right this minute? Asset.is_active() folds the date window with
    # the day-of-week filter and the play_time_from/to slot. If those
    # exclude today/now, the asset is enabled and 'within window' but
    # not on screen — call it "Scheduled" so we don't lie about "Live".
    is_active = asset.is_active() if hasattr(asset, 'is_active') else True
    delta = end - now
    if not is_active:
        return {
            'kind': 'scheduled',
            'primary': 'Scheduled · off-window now',
            'secondary': secondary,
        }
    if delta.days >= 365:
        primary = 'Live · open-ended'
    else:
        primary = f'Live · ends {_relative_phrase(delta, future=True)}'
    return {'kind': 'live', 'primary': primary, 'secondary': secondary}


def _relative_phrase(delta: 'timedelta', *, future: bool) -> str:
    """Compact relative duration: 'in 3 days' / '2 days ago' / 'in 5h'."""
    seconds = int(delta.total_seconds())
    if seconds < 60:
        unit = 'now'
    elif seconds < 3600:
        unit = f'{seconds // 60}m'
    elif seconds < 86400:
        unit = f'{seconds // 3600}h'
    elif seconds < 86400 * 30:
        days = seconds // 86400
        unit = f'{days} day{"s" if days != 1 else ""}'
    elif seconds < 86400 * 365:
        months = seconds // (86400 * 30)
        unit = f'{months} month{"s" if months != 1 else ""}'
    else:
        years = seconds // (86400 * 365)
        unit = f'{years} year{"s" if years != 1 else ""}'
    return f'in {unit}' if future else f'{unit} ago'


@register.filter
def humanize_duration(value: Any) -> str:
    """Format a duration in seconds as 'Xh Ym', 'Xm Ys', or 'Xs'.

    Asset.duration is stored as integer seconds. The schedule table
    used to render '42 sec' / '3600 sec' which scans poorly for long
    streams; this filter renders the same value as '42s', '1m 30s',
    '1h 5m'. Drops the seconds component once we're into hours so
    a 1h05m02s stream doesn't read like a stopwatch.
    """
    try:
        total = int(value)
    except (TypeError, ValueError):
        return ''
    if total <= 0:
        return '0s'
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f'{hours}h')
        if minutes:
            parts.append(f'{minutes}m')
        return ' '.join(parts)
    if minutes:
        parts.append(f'{minutes}m')
        if seconds:
            parts.append(f'{seconds}s')
        return ' '.join(parts)
    return f'{seconds}s'
