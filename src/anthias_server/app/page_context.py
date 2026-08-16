"""Page-context helpers for server-rendered Django views.

Each function returns the dict a corresponding template needs.
The DRF API views in api/views/v2.py call the same primitives
(diagnostics, device_helper, settings) so the JSON and HTML
surfaces stay in lockstep without going through the HTTP API.
"""

import functools
import logging
import os
import zoneinfo
from datetime import timedelta
from os import getenv, statvfs
from typing import Any

import psutil
from django.template.defaultfilters import filesizeformat

from anthias_common import device_helper, storage_health, undervoltage
from anthias_common.board import LOW_RAM_THRESHOLD_KB
from anthias_common.utils import (
    clamp_screen_rotation,
    connect_to_redis,
    get_node_mac_address,
    is_balena_app,
)
from anthias_server.lib import diagnostics, display_power
from anthias_server.lib.github import is_up_to_date
from anthias_server.lib.timezone import format_utc_offset
from anthias_server.settings import settings

_redis = connect_to_redis()
logger = logging.getLogger(__name__)

# One log line per process for a broken diagnostic, so a persistent
# failure is visible without repeating on every page render.
_logged_power_failure = False


def _parse_iso(value: Any) -> Any:
    """ISO string from the Redis latch → aware datetime, or ``None``.

    The latch stores ISO strings because it round-trips through JSON;
    templates want datetimes so they can use ``|naturaltime`` and
    render "8 minutes ago" instead of a UTC timestamp an operator
    would have to convert in their head.

    A naive value is forced to UTC rather than returned as-is. We only
    ever write offset-aware strings, but ``fromisoformat`` happily
    parses a naive one, and ``naturaltime`` then compares it against a
    naive *local* now instead of an aware UTC one. On a device in any
    non-UTC zone that renders the offset as elapsed time: a brown-out
    one minute ago on a US-Eastern player shows as "3 hours from now".
    Stamping UTC is correct rather than merely defensive, because UTC
    is what the writer emits.
    """
    from datetime import UTC, datetime

    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _power_warning() -> dict[str, Any] | None:
    """Under-voltage banner state, or ``None`` when there's nothing
    to say.

    Returned from :func:`navbar` so the banner renders on every page
    rather than only on System Info. An operator whose screen is
    glitching goes to the Schedule page to look at their content, not
    to a diagnostics tab, so that is where the explanation has to
    meet them.

    A power supply that can't keep up corrupts the SD card over time,
    which is a much worse outcome than the visible glitching that
    usually prompts the support ticket, hence a persistent banner
    rather than a dismissible toast.
    """
    try:
        state = undervoltage.get_state(_redis)
    except Exception:
        # Never let a diagnostic break page rendering, but do not fail
        # silently either: this returns None, which renders no banner,
        # so a broken probe is indistinguishable from a healthy supply
        # and would hide a real under-voltage from the operator.
        global _logged_power_failure
        if not _logged_power_failure:
            _logged_power_failure = True
            logger.warning(
                'Could not read under-voltage state for the banner; '
                'power warnings are not being shown.',
                exc_info=True,
            )
        return None

    if not undervoltage.should_warn(state):
        return None

    return {
        'active': state['active'],
        'seen_since_boot': state['seen_since_boot'],
        'count': state['count'],
        'first_seen': _parse_iso(state['first_seen']),
        'last_seen': _parse_iso(state['last_seen']),
    }


def _power_state() -> dict[str, Any]:
    """Full under-voltage state for the System Info card.

    Unlike :func:`_power_warning` this always returns a dict: the
    card reports "no problems detected" and "not supported on this
    device" as well as the alert.
    """
    try:
        state = undervoltage.get_state(_redis)
    except Exception:
        state = {
            'supported': False,
            'active': False,
            'seen_since_boot': False,
            'first_seen': None,
            'last_seen': None,
            'count': 0,
        }
    state['warn'] = undervoltage.should_warn(state)
    state['first_seen'] = _parse_iso(state['first_seen'])
    state['last_seen'] = _parse_iso(state['last_seen'])
    return state


def _blank_storage_state() -> dict[str, Any]:
    """What the storage helpers report when the probe itself failed.

    ``supported`` false rather than a healthy-looking zero: the card
    might be fine or might be on fire, and the UI says which of those
    it knows.
    """
    return {
        'supported': False,
        'status': storage_health.STATUS_UNKNOWN,
        'media': {'kind': 'unknown'},
    }


def _storage_state() -> dict[str, Any]:
    try:
        return storage_health.get_state(_redis, settings.get_configdir())
    except Exception:
        # Never let a diagnostic break page rendering.
        return _blank_storage_state()


# Verdict → the shape of thing that has gone wrong, which is what
# picks the copy. Deliberately finer-grained than the API's ``status``:
# "the card went read-only" and "the card is returning corrupt data"
# are the same severity and the same fix, but an operator recognises
# their own symptom in one of them and not the other, and being
# recognised is what gets the banner read instead of dismissed.
_STORAGE_CRITICAL_KINDS = ('full', 'read_only', 'write_failing', 'errors_now')

# What to call the thing on screen. Anthias runs from an SD card on
# most of the fleet, from soldered eMMC on the compute modules, and
# from an SSD on x86, so a hardcoded "memory card" would be plainly
# wrong on two of the three and would cost the warning its
# credibility with the operator reading it.
_STORAGE_NOUNS = {
    'sd': 'memory card',
    'emmc': 'built-in storage',
    'disk': 'drive',
}


def _bad_blocks(media: dict[str, Any] | None) -> int:
    """Blocks the drive has found bad, across both SMART dialects.

    ``reallocated``/``pending`` are ATA-only fields and NVMe reports
    the same class of fault as ``media_errors``. Counting only the ATA
    pair meant an NVMe drive with media errors fell through to the
    wear copy and was told it had "used N% of the writes it was built
    for" -- the mismatched story :func:`_storage_kind` exists to
    prevent, just on the other bus.
    """
    smart = (media or {}).get('smart') or {}
    return (
        (smart.get('reallocated_sectors') or 0)
        + (smart.get('pending_sectors') or 0)
        + (smart.get('media_errors') or 0)
    )


def _storage_kind(state: dict[str, Any]) -> str | None:
    status = state.get('status')

    if status == storage_health.STATUS_FULL:
        return 'full'
    if status == storage_health.STATUS_FAILING:
        # Ordered by how directly the operator feels it. A read-only
        # filesystem is the one they will already have noticed
        # (nothing they change sticks), so it wins over the reasons
        # that produced it.
        if state.get('read_only'):
            return 'read_only'
        if state.get('write_ok') is False or state.get(
            'write_failed_since_boot'
        ):
            return 'write_failing'
        return 'errors_now'
    if status == storage_health.STATUS_ERRORS:
        return 'errors_past'
    if status == storage_health.STATUS_WEAR:
        # Wear and bad blocks are the same severity and reach the same
        # verdict, but they are not the same story and they do not
        # have the same fix. Measured on the x86 testbed: an SSD with
        # zero wear (Wear_Leveling_Count at 100) and a PASSED overall
        # self-assessment, but 4 reallocated and 4 pending sectors.
        # Telling that operator their drive is "worn out" sends them
        # looking at write volume when the drive is actually failing
        # to read blocks it already wrote.
        if _bad_blocks(state.get('media')):
            return 'bad_sectors'
        return 'wear'
    return None


def _storage_warning() -> dict[str, Any] | None:
    """Memory-card banner state, or ``None`` when there's nothing to
    say.

    Returned from :func:`navbar` alongside the under-voltage banner,
    for the same reason: the two failures a Raspberry Pi actually dies
    of are a bad power supply and a worn-out card, and neither is
    something an operator goes looking for on a diagnostics page.

    The pair is also causally linked, which is why the copy for one
    mentions the other. Under-voltage is one of the main things that
    corrupts cards, so a device showing both banners has one problem,
    not two.
    """
    state = _storage_state()
    if not storage_health.should_warn(state):
        return None

    kind = _storage_kind(state)
    if kind is None:
        return None

    media = state.get('media') or {}
    media_kind = str(media.get('kind') or 'unknown')
    return {
        'kind': kind,
        'severity': 'critical'
        if kind in _STORAGE_CRITICAL_KINDS
        else 'warning',
        # eMMC is soldered to the board, so "replace the card" is
        # advice the operator physically cannot follow. The template
        # swaps it for the module swap instead.
        'replaceable': media_kind != 'emmc',
        'media_kind': media_kind,
        'media_noun': _STORAGE_NOUNS.get(media_kind, 'storage'),
        'errors_count': state.get('errors_count'),
        # Errors seen during THIS boot. errors_count is ext4's
        # superblock counter and is cumulative over the filesystem's
        # life, so copy that says "since it last restarted" has to
        # use this one or it reports a five-year-old total as today's
        # news.
        'errors_new': state.get('errors_new') or 0,
        'last_error': _parse_iso(state.get('last_error')),
        'write_reason': state.get('write_reason'),
        'wear_pct': media.get('wear_pct'),
        'wear_is_exact': bool(media.get('wear_is_exact')),
        'pre_eol': media.get('pre_eol'),
        # Counted here rather than in the template so the copy can
        # state a number instead of hedging.
        'bad_sectors': _bad_blocks(media),
    }


def _storage_card() -> dict[str, Any]:
    """Full storage detail for the System Info card.

    Unlike :func:`_storage_warning` this always returns a dict: the
    card reports "no problems detected" and "we couldn't check" as
    well as the alert.
    """
    # Copied before mutating: get_state builds a fresh dict per call
    # in production, but this function has no way to know that, and
    # _parse_iso is destructive on a second application -- it returns
    # None for an already-parsed datetime, so re-decorating the same
    # dict would silently blank every timestamp. Not mutating a value
    # we did not create removes the whole question.
    state = dict(_storage_state())
    state['warn'] = storage_health.should_warn(state)
    state['kind'] = _storage_kind(state)
    state['first_error'] = _parse_iso(state.get('first_error'))
    state['last_error'] = _parse_iso(state.get('last_error'))
    state['last_check'] = _parse_iso(state.get('last_check'))

    # Lifetime writes are the closest thing a plain SD card gives to a
    # wear figure, and the raw kilobyte count means nothing to anyone.
    written_kb = state.get('lifetime_written_kb')
    state['lifetime_written'] = (
        filesizeformat(written_kb * 1024) if written_kb else None
    )

    state['bad_sectors'] = _bad_blocks(state.get('media'))
    return state


def navbar() -> dict[str, Any]:
    """Shared by every page; merged into context by helpers.template()."""
    return {
        'is_balena': is_balena_app(),
        'up_to_date': is_up_to_date(),
        'player_name': settings['player_name'],
        'power_warning': _power_warning(),
        'storage_warning': _storage_warning(),
    }


def _resolved_resolution() -> dict[str, Any]:
    """Active display resolution (reported by the viewer) with a
    fallback to the operator-configured value from settings.

    The viewer publishes 'viewer:display_resolution' to Redis every
    minute with a 3-minute TTL. When the key is present we trust it
    over the configured value — that's the actual screen the player
    is rendering to. When it's absent (key expired, viewer never
    detected an output, single-process dev runs), we surface the
    configured value with a 'configured' label so the operator knows
    it's the requested mode rather than the live one.
    """
    live = _redis.get('viewer:display_resolution')
    if live:
        return {'value': live, 'source': 'live'}
    return {'value': settings['resolution'], 'source': 'configured'}


def system_info() -> dict[str, Any]:
    from django.utils import timezone
    from django.utils.timesince import timesince

    slash = statvfs('/')
    virtual_memory = psutil.virtual_memory()
    disk_total = slash.f_blocks * slash.f_frsize
    disk_free = slash.f_bavail * slash.f_frsize
    disk_used = max(0, disk_total - disk_free)
    uptime = timedelta(seconds=diagnostics.get_uptime())
    device_model, device_model_detail = device_helper.get_device_model_parts()

    anthias_version = diagnostics.get_anthias_version()
    anthias_version_head = diagnostics.get_anthias_version_head()
    anthias_version_meta = diagnostics.get_anthias_version_meta()

    # Pie-friendly breakdown — three slices that sum to total. psutil's
    # `used` already excludes buffers/cache on Linux (matches `free -m`),
    # `available` is what new processes can claim before swapping.
    # Cache estimate = total − used − free; clamped to ≥0 so kernels
    # that report differently still produce sane geometry.
    mem_total = virtual_memory.total >> 20
    mem_used = virtual_memory.used >> 20
    mem_free = virtual_memory.free >> 20
    mem_cache = max(0, mem_total - mem_used - mem_free)

    def _pct(n: int, total: int) -> float:
        return round((n / total) * 100, 1) if total else 0.0

    # Surface all three load-average windows so the System Info card
    # can render trend (1m vs 15m) instead of just a single number.
    # Bars are sized as a fraction of the CPU count — load == nproc
    # means the system is exactly saturated. Cap the headroom at
    # 1.5×nproc so a single runaway process doesn't drown out the
    # baseline. trend ∈ {'up', 'down', 'stable'} drives the arrow.
    cpu_count = os.cpu_count() or 1
    load_raw = diagnostics.get_load_avg()
    load_1m = load_raw['1 min']
    load_5m = load_raw['5 min']
    load_15m = load_raw['15 min']
    load_scale = max(cpu_count * 1.5, max(load_1m, load_5m, load_15m, 0.5))
    if load_15m == 0:
        load_trend = 'stable'
    elif load_1m > load_15m * 1.1:
        load_trend = 'up'
    elif load_1m < load_15m * 0.9:
        load_trend = 'down'
    else:
        load_trend = 'stable'

    def _load_bar(value: float) -> dict[str, float | str]:
        pct = round((value / load_scale) * 100, 1) if load_scale else 0.0
        if value >= cpu_count:
            severity = 'high'
        elif value >= cpu_count * 0.7:
            severity = 'warn'
        else:
            severity = 'ok'
        return {'value': value, 'pct': pct, 'severity': severity}

    # Snapshot the local instant once so iso/offset can't straddle a
    # second boundary, and resolve the active zone name a single time.
    now_local = timezone.localtime(timezone.now())
    tz_name = timezone.get_current_timezone_name()

    return {
        'loadavg': load_15m,
        'load': {
            'cpu_count': cpu_count,
            'trend': load_trend,
            'windows': [
                (_load_bar(load_1m), '1 min'),
                (_load_bar(load_5m), '5 min'),
                (_load_bar(load_15m), '15 min'),
            ],
        },
        'free_space': filesizeformat(disk_free),
        'disk': {
            'total_human': filesizeformat(disk_total),
            'used_human': filesizeformat(disk_used),
            'free_human': filesizeformat(disk_free),
            'used_pct': _pct(disk_used, disk_total),
            'free_pct': _pct(disk_free, disk_total),
        },
        'memory': {
            'total': mem_total,
            'used': mem_used,
            'free': mem_free,
            'shared': virtual_memory.shared >> 20,
            'buff': virtual_memory.buffers >> 20,
            'available': virtual_memory.available >> 20,
            'cache': mem_cache,
            'used_pct': _pct(mem_used, mem_total),
            'cache_pct': _pct(mem_cache, mem_total),
            'free_pct': _pct(mem_free, mem_total),
        },
        # Surface the low-RAM gate alongside Memory so an operator
        # whose 4K HEVC upload was rejected (or who notices an asset
        # crossfade has degraded into a hard cut) can see *why* on
        # the same screen. ``threshold_mib`` is the same cutoff
        # ``is_low_ram_device`` consults — exposing it keeps the
        # operator from having to spelunk the codebase to learn what
        # qualifies. Compares against psutil's measurement (same
        # /proc/meminfo source host_agent reads) so the page is
        # accurate even if host_agent hasn't published yet.
        'low_ram': {
            'active': virtual_memory.total < LOW_RAM_THRESHOLD_KB * 1024,
            'threshold_mib': LOW_RAM_THRESHOLD_KB >> 10,
        },
        # Full under-voltage detail for the System Info card. The
        # banner (see _power_warning) only carries enough to render
        # the alert; this adds the "power supply is fine" and
        # "this device can't report it" cases, which are worth
        # stating explicitly on a diagnostics page. An operator
        # chasing a glitch needs to know whether we checked and
        # found nothing, or never checked at all.
        'power': _power_state(),
        # Full storage detail, next to Disk usage on purpose: "83% of
        # 32 GB used" and "this card has recorded 6 filesystem errors"
        # are the two halves of one question an operator asks about
        # storage, and splitting them across the page would make the
        # second one findable only by someone who already suspected
        # it.
        'storage': _storage_card(),
        # Uptime as "2 days, 3 hours" via Django's timesince — pass the
        # boot-time so timesince computes against now(). Pass depth=2 so
        # 'X year, Y month' style formats stay readable on long-lived
        # devices instead of the 6-segment default.
        'uptime': {
            'days': uptime.days,
            'hours': round(uptime.seconds / 3600, 2),
            'human': timesince(timezone.now() - uptime, depth=2),
        },
        # The device's own wall clock + active timezone, so an operator
        # can confirm what "now" the scheduler is using (issue #1755).
        # Seeded from the server instant (``iso``) rather than the
        # browser's clock — the whole point is to reveal a *wrong*
        # device clock, which trusting the browser would mask. The
        # offset is formatted +HH:MM for readability.
        'device_time': {
            # Seconds precision: microseconds from isoformat() aren't
            # parsed by every JS Date.parse() engine, and a NaN seed
            # would leave the live clock never starting.
            'iso': now_local.isoformat(timespec='seconds'),
            # Raw IANA id for the JS Intl formatter (data-timezone)...
            'timezone': tz_name,
            # ...and a humanised version for the visible sub-label.
            'timezone_label': tz_name.replace('_', ' '),
            'offset': format_utc_offset(now_local),
        },
        'display_power': _redis.get('display_power'),
        'resolution': _resolved_resolution(),
        'device_model': device_model,
        'device_model_detail': device_model_detail,
        'anthias_version': anthias_version,
        'anthias_version_head': anthias_version_head,
        'anthias_version_meta': anthias_version_meta,
        'mac_address': get_node_mac_address(),
        'host_user': getenv('HOST_USER'),
    }


_DATE_FORMAT_OPTIONS = (
    ('mm/dd/yyyy', 'month/day/year'),
    ('dd/mm/yyyy', 'day/month/year'),
    ('yyyy/mm/dd', 'year/month/day'),
    ('mm-dd-yyyy', 'month-day-year'),
    ('dd-mm-yyyy', 'day-month-year'),
    ('yyyy-mm-dd', 'year-month-day'),
    ('mm.dd.yyyy', 'month.day.year'),
    ('dd.mm.yyyy', 'day.month.year'),
    ('yyyy.mm.dd', 'year.month.day'),
)

# Python weekday numbering (Monday=0 ... Sunday=6), matching what
# lib/display_power.py parses and what datetime.weekday() returns.
_WEEKDAY_OPTIONS = (
    (0, 'Mon'),
    (1, 'Tue'),
    (2, 'Wed'),
    (3, 'Thu'),
    (4, 'Fri'),
    (5, 'Sat'),
    (6, 'Sun'),
)


@functools.lru_cache(maxsize=1)
def _timezone_options() -> tuple[tuple[str, str], ...]:
    """(value, label) pairs for the Settings timezone dropdown.

    Leading blank entry ("System default") defers to
    resolve_time_zone() — TZ env, then /etc/timezone, then UTC. The
    rest is the sorted IANA zone list; on balena the host is always
    UTC, so this dropdown is the only way to schedule/display in local
    time there. Cached — the set is fixed for the life of the process.
    """
    # Value stays the real IANA id (what Django/Intl need); the label
    # humanises the underscore so options read "America/New York".
    zones = sorted(zoneinfo.available_timezones())
    return (('', 'System default'),) + tuple(
        (z, z.replace('_', ' ')) for z in zones
    )


def device_settings() -> dict[str, Any]:
    """Form values + dropdown choices for /settings.

    Pulls from the live settings object (no API hop). Adds the
    page-only state the React component used to track:
    `has_saved_basic_auth` (whether to show the Current Password
    field), `is_pi5` (whether to hide the 3.5mm jack option), and
    the choice tuples for the auth_backend / date_format dropdowns.
    """
    from anthias_server.lib.auth import _persisted_operator, operator_username

    settings.load()
    # parse_cpu_info() returns Mapping[str, int | str] per its stub, so
    # cast to str before substring-checking against the Pi 5 model name —
    # mypy refuses `'X' in (int|str)` even though str-len-check works.
    device_model = str(device_helper.parse_cpu_info().get('model') or '')

    # ``has_saved_basic_auth`` keys the "Current password" field on the
    # settings page. It needs to be true any time the device has a
    # persisted operator User row — whether or not auth is currently
    # enabled — because re-enabling auth requires proving knowledge
    # of the existing password (see the ``apply_auth_settings``
    # privilege-escalation guard). Hiding the field when auth is
    # disabled but a User exists would mask the field the operator is
    # required to fill in to make the form succeed.
    has_persisted_operator = _persisted_operator() is not None

    return {
        'player_name': settings['player_name'],
        'default_duration': settings['default_duration'],
        'default_streaming_duration': settings['default_streaming_duration'],
        'audio_output': settings['audio_output'],
        'date_format': settings['date_format'],
        'timezone': settings['timezone'],
        'auth_backend': settings['auth_backend'],
        'username': operator_username(),
        'show_splash': settings['show_splash'],
        'default_assets': settings['default_assets'],
        'shuffle_playlist': settings['shuffle_playlist'],
        'use_24_hour_clock': settings['use_24_hour_clock'],
        'debug_logging': settings['debug_logging'],
        'prefer_dark_mode': settings['prefer_dark_mode'],
        'verify_ssl': settings['verify_ssl'],
        # Clamp on the read side so a stale conf value (e.g. an
        # old 45 from a hand-edit) doesn't leave the dropdown with no
        # ``selected`` option — the template's {% if screen_rotation
        # == 0 %} ladder picks 0° in that case, matching the
        # viewer's runtime clamp (Copilot review of #2882).
        'screen_rotation': clamp_screen_rotation(settings['screen_rotation']),
        # Auth-form chrome
        'has_saved_basic_auth': has_persisted_operator
        or settings['auth_backend'] == 'auth_basic',
        # Hide the 3.5mm jack option on Pi 5 — the jack moved off-board
        # on that revision (matches the React audio-output dropdown).
        'is_pi5': 'Raspberry Pi 5' in device_model,
        'date_format_options': _DATE_FORMAT_OPTIONS,
        'timezone_options': _timezone_options(),
        # Render-time gate for the experimental CEC display-power
        # buttons. cec_available() reads a single Redis key the viewer
        # publishes at startup — not a device probe and not a round trip
        # to the viewer — so it stays cheap enough to call on every
        # settings render.
        'cec_available': diagnostics.cec_available(),
        # The schedule is NOT gated on cec_available(): when no CEC
        # display answers it falls back to the viewer's local blanking,
        # so it is useful on boards with a plain monitor too.
        'display_power_schedule_enabled': settings[
            'display_power_schedule_enabled'
        ],
        'display_power_on_time': settings['display_power_on_time'],
        'display_power_off_time': settings['display_power_off_time'],
        # Parsed with the same helper the beat uses. An inline
        # comprehension here had the opposite empty-input behaviour —
        # it rendered *zero* days checked where parse_days reads the
        # same value as *every* day, so the settings page would show a
        # schedule running on no days while it actually ran daily.
        'display_power_days': sorted(
            display_power.parse_days(settings['display_power_days'])
        ),
        'weekday_options': _WEEKDAY_OPTIONS,
    }


def assets() -> dict[str, Any]:
    """Active + inactive asset lists for /.

    Partition matches what the operator can change directly from the
    home page: `is_enabled` (the Activity toggle in the row) AND
    NOT `is_processing` (transient upload-in-progress state). The
    stricter `Asset.is_active()` predicate also factors in the date
    range and the day-of-week / time-of-day window — that's what
    the scheduler/viewer use to decide what to play right now, but
    using it here would yank a row out of the Active section just
    because today's weekday isn't in the asset's play_days, and the
    operator would have no way to flip it back without editing the
    schedule. React's UI used the same operator-facing split.
    """
    from anthias_server.app.models import Asset

    qs = Asset.objects.all()
    active: list[Asset] = []
    inactive: list[Asset] = []
    for asset in qs:
        if asset.is_enabled and not asset.is_processing:
            active.append(asset)
        else:
            inactive.append(asset)
    active.sort(key=lambda a: a.play_order)
    inactive.sort(key=lambda a: a.play_order)
    from anthias_server.app.models import REFRESH_INTERVAL_S_MAX

    return {
        'active_assets': active,
        'inactive_assets': inactive,
        # Render the auto-refresh input's ``max`` attribute from the
        # same constant the v2 serializer / form handler use, so the
        # client-side and server-side caps can't drift.
        'refresh_interval_s_max': REFRESH_INTERVAL_S_MAX,
    }


def integrations() -> dict[str, Any]:
    data: dict[str, Any] = {'is_balena': is_balena_app()}
    if data['is_balena']:
        data.update(
            {
                'balena_device_id': getenv('BALENA_DEVICE_UUID'),
                'balena_app_id': getenv('BALENA_APP_ID'),
                'balena_app_name': getenv('BALENA_APP_NAME'),
                'balena_supervisor_version': getenv(
                    'BALENA_SUPERVISOR_VERSION'
                ),
                'balena_host_os_version': getenv('BALENA_HOST_OS_VERSION'),
                'balena_device_name_at_init': getenv(
                    'BALENA_DEVICE_NAME_AT_INIT'
                ),
            }
        )
    return data
