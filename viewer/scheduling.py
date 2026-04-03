import logging
import threading
from datetime import timedelta
from os import path
import secrets

from django.utils import timezone

from anthias_app.models import Asset, ScheduleSlot, ScheduleSlotItem
from settings import settings

_sysrandom = secrets.SystemRandom()


def _secure_shuffle(lst):
    """Shuffle list in-place using a cryptographically secure RNG."""
    _sysrandom.shuffle(lst)


def _set_time(dt, t, second=0):
    """Replace time components on a datetime, zeroing microseconds."""
    return dt.replace(
        hour=t.hour,
        minute=t.minute,
        second=second,
        microsecond=0,
    )


def get_specific_asset(asset_id):
    logging.info('Getting specific asset')
    try:
        return Asset.objects.get(asset_id=asset_id).__dict__
    except Asset.DoesNotExist:
        logging.debug('Asset %s not found in database', asset_id)
        return None


def _asset_to_dict(asset, duration_override=None):
    """Convert an Asset to the dict format expected by the viewer."""
    d = {k: v for k, v in asset.__dict__.items() if k not in ['_state', 'md5']}
    if duration_override is not None:
        d['duration'] = duration_override
    return d


def generate_asset_list(skip_event_id=None):
    """Build the playlist for the viewer.

    If ScheduleSlot records exist the viewer enters "schedule mode":
    only assets linked to the currently-active slot are returned.
    Otherwise falls back to legacy behaviour (asset.is_active()).

    Returns (playlist, deadline, no_loop, active_slot_id).
    """
    logging.info('Generating asset-list...')

    slots = list(ScheduleSlot.objects.all())
    if slots:
        return _generate_schedule_playlist(
            slots,
            skip_event_id=skip_event_id,
        )

    playlist, deadline = _generate_legacy_playlist()
    return playlist, deadline, False, None


def _generate_legacy_playlist():
    """Original Anthias playlist generation -- no schedule slots."""
    assets = Asset.objects.all()
    deadlines = [
        asset.end_date if asset.is_active() else asset.start_date
        for asset in assets
    ]

    enabled_assets = Asset.objects.filter(
        is_enabled=True,
        start_date__isnull=False,
        end_date__isnull=False,
    ).order_by('play_order')
    playlist = [
        _asset_to_dict(asset) for asset in enabled_assets if asset.is_active()
    ]

    deadline = sorted(deadlines)[0] if len(deadlines) > 0 else None
    logging.debug(
        'legacy playlist: %d assets, deadline %s',
        len(playlist),
        deadline,
    )

    if settings['shuffle_playlist']:
        _secure_shuffle(playlist)

    return playlist, deadline


def _generate_schedule_playlist(slots, skip_event_id=None):
    """Schedule-aware playlist: find active slot, return its items.

    Priority: event > time > default.
    Returns (playlist, deadline, no_loop, active_slot_id).
    """
    active_event = None
    active_time = None
    default_slot = None

    for slot in slots:
        if slot.is_default:
            default_slot = slot
        elif slot.slot_type == 'event' and slot.is_currently_active():
            if skip_event_id and slot.slot_id == skip_event_id:
                continue
            if active_event is None:
                active_event = slot
        elif slot.is_currently_active():
            if active_time is None:
                active_time = slot

    active_slot = None
    for candidate in [active_event, active_time, default_slot]:
        if candidate is None:
            continue
        has_items = ScheduleSlotItem.objects.filter(
            slot=candidate,
        ).exists()
        if has_items:
            active_slot = candidate
            break

    if active_slot is None:
        active_slot = active_event or active_time or default_slot

    if active_slot is None:
        deadline = _calc_next_slot_start(
            [s for s in slots if not s.is_default],
        )
        logging.info(
            'schedule: no active slot, next start at %s',
            deadline,
        )
        return [], deadline, False, None

    no_loop = getattr(active_slot, 'no_loop', False)

    logging.info(
        'schedule: active slot "%s" (type=%s, default=%s, no_loop=%s)',
        active_slot.name,
        getattr(active_slot, 'slot_type', 'time'),
        active_slot.is_default,
        no_loop,
    )

    items = (
        ScheduleSlotItem.objects.filter(slot=active_slot)
        .select_related('asset')
        .order_by('sort_order')
    )

    playlist = []
    for item in items:
        asset = item.asset
        if not asset.is_enabled:
            continue
        playlist.append(
            _asset_to_dict(asset, item.duration_override),
        )

    if not no_loop and settings['shuffle_playlist']:
        _secure_shuffle(playlist)

    deadline = _calc_slot_deadline(active_slot, slots)
    logging.debug(
        'schedule playlist: %d assets from slot "%s", deadline %s, no_loop %s',
        len(playlist),
        active_slot.name,
        deadline,
        no_loop,
    )

    return playlist, deadline, no_loop, active_slot.slot_id


def _calc_slot_deadline(active_slot, all_slots):
    """When does the current slot end (= time to re-evaluate)?"""
    now = timezone.localtime()
    current_time = now.time()

    if active_slot.is_default:
        return _calc_next_slot_start(
            [s for s in all_slots if not s.is_default],
        )

    if getattr(active_slot, 'slot_type', 'time') == 'event':
        return _set_time(now, active_slot.time_to, active_slot.time_to.second)

    if active_slot.is_overnight and current_time >= active_slot.time_from:
        base = now + timedelta(days=1)
    else:
        base = now
    slot_end = _set_time(base, active_slot.time_to)

    event_slots = [
        s
        for s in all_slots
        if (getattr(s, 'slot_type', 'time') == 'event' and not s.is_default)
    ]
    if event_slots:
        next_event = _calc_next_slot_start(event_slots)
        if next_event and next_event < slot_end:
            return next_event

    return slot_end


def _calc_next_slot_start(non_default_slots):
    """Find the nearest future moment when any slot starts."""
    now = timezone.localtime()
    candidates = []

    for slot in non_default_slots:
        days = slot.get_days_of_week()

        if not days and getattr(slot, 'start_date', None):
            base = now.replace(
                year=slot.start_date.year,
                month=slot.start_date.month,
                day=slot.start_date.day,
            )
            candidate = _set_time(base, slot.time_from)
            if candidate > now:
                candidates.append(candidate)
            continue

        for day_offset in range(8):
            check_date = now + timedelta(days=day_offset)
            check_weekday = check_date.isoweekday()
            if days and check_weekday not in days:
                continue
            candidate = _set_time(check_date, slot.time_from)
            if candidate > now:
                candidates.append(candidate)
                break

    return min(candidates) if candidates else None


class Scheduler(object):
    def __init__(self, *args, **kwargs):
        logging.debug('Scheduler init')
        self.assets = []
        self.counter = 0
        self.current_asset_id = None
        self.deadline = None
        self.extra_asset = None
        self.index = 0
        self.reverse = 0
        self.no_loop = False
        self.no_loop_done = False
        self._active_slot_id = None
        self._completed_event_id = None
        self._deadline_timer = None
        self.update_playlist()

    def get_next_asset(self):
        logging.debug('get_next_asset')

        if self.extra_asset is not None:
            asset = get_specific_asset(self.extra_asset)
            if asset and not asset['is_processing']:
                self.current_asset_id = self.extra_asset
                self.extra_asset = None
                return asset
            logging.error('Asset not found or processed')
            self.extra_asset = None

        self.refresh_playlist()
        logging.debug('get_next_asset after refresh')
        if not self.assets:
            self.current_asset_id = None
            return None

        if self.reverse:
            idx = (self.index - 2) % len(self.assets)
            self.index = (self.index - 1) % len(self.assets)
            self.reverse = False
        else:
            idx = self.index
            self.index = (self.index + 1) % len(self.assets)

        if self.no_loop and self.index == 0:
            self.no_loop_done = True
            logging.info(
                'Event slot: finished last item, no_loop_done=True',
            )

        logging.debug(
            'get_next_asset counter %s returning asset %s of %s',
            self.counter,
            idx + 1,
            len(self.assets),
        )

        if (
            settings['shuffle_playlist']
            and self.index == 0
            and not self.no_loop
        ):
            self.counter += 1

        current_asset = self.assets[idx]
        self.current_asset_id = current_asset.get('asset_id')
        return current_asset

    def refresh_playlist(self):
        logging.debug('refresh_playlist')
        time_cur = timezone.now()

        logging.debug(
            'refresh: counter: (%s) deadline (%s) timecur (%s) no_loop (%s)',
            self.counter,
            self.deadline,
            time_cur,
            self.no_loop,
        )

        if self.no_loop and self.no_loop_done:
            logging.info(
                'Event slot finished, resuming normal schedule immediately',
            )
            self._completed_event_id = self._active_slot_id
            self.no_loop = False
            self.no_loop_done = False
            self.update_playlist(from_event_done=True)
            return

        if self.get_db_mtime() > self.last_update_db_mtime:
            logging.debug(
                'updating playlist due to database modification',
            )
            self.update_playlist()
        elif settings['shuffle_playlist'] and self.counter >= 5:
            self.update_playlist()
        elif self.deadline and self.deadline <= time_cur:
            self.update_playlist()

    def _start_deadline_timer(self):
        """Start a timer that fires when the deadline arrives."""
        self._cancel_deadline_timer()
        if not self.deadline:
            return
        now = timezone.now()
        delay = (self.deadline - now).total_seconds()
        if delay <= 0:
            return
        logging.info(
            'Deadline timer started: %.1fs until %s',
            delay,
            self.deadline,
        )
        t = threading.Timer(delay, self._on_deadline)
        t.daemon = True
        t.start()
        self._deadline_timer = t

    def _on_deadline(self):
        """Called when the deadline timer fires."""
        logging.info(
            'Deadline reached, interrupting current asset for schedule change',
        )
        from viewer.playback import skip_event

        skip_event.set()

    def _cancel_deadline_timer(self):
        """Cancel the deadline timer if it is active."""
        if self._deadline_timer is not None:
            self._deadline_timer.cancel()
            self._deadline_timer = None

    def update_playlist(self, from_event_done=False):
        logging.debug(
            'update_playlist (from_event_done=%s)',
            from_event_done,
        )
        self._cancel_deadline_timer()
        self.last_update_db_mtime = self.get_db_mtime()

        skip_id = self._completed_event_id if from_event_done else None
        if not from_event_done:
            self._completed_event_id = None

        (
            new_assets,
            new_deadline,
            new_no_loop,
            new_slot_id,
        ) = generate_asset_list(skip_event_id=skip_id)

        if (
            new_assets == self.assets
            and new_deadline == self.deadline
            and new_no_loop == self.no_loop
        ):
            self._start_deadline_timer()
            return

        self.assets, self.deadline = new_assets, new_deadline
        self.no_loop = new_no_loop
        self._active_slot_id = new_slot_id
        self.no_loop_done = False
        self.counter = 0
        self.index = self.index % len(self.assets) if self.assets else 0
        logging.debug(
            'update_playlist done, count %s, counter %s, '
            'index %s, deadline %s, no_loop %s, slot %s',
            len(self.assets),
            self.counter,
            self.index,
            self.deadline,
            self.no_loop,
            new_slot_id,
        )
        self._start_deadline_timer()

    def get_db_mtime(self):
        try:
            return path.getmtime(settings['database'])
        except (OSError, TypeError):
            return 0
