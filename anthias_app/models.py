import json
import uuid

from django.db import models
from django.utils import timezone


def generate_asset_id():
    return uuid.uuid4().hex


def _default_all_days():
    return '[1,2,3,4,5,6,7]'


SLOT_TYPE_CHOICES = [
    ('default', 'Default'),
    ('time', 'Time'),
    ('event', 'Event'),
]


class Asset(models.Model):
    asset_id = models.TextField(
        primary_key=True, default=generate_asset_id, editable=False
    )
    name = models.TextField(blank=True, null=True)
    uri = models.TextField(blank=True, null=True)
    md5 = models.TextField(blank=True, null=True)
    start_date = models.DateTimeField(blank=True, null=True)
    end_date = models.DateTimeField(blank=True, null=True)
    duration = models.BigIntegerField(blank=True, null=True)
    mimetype = models.TextField(blank=True, null=True)
    is_enabled = models.BooleanField(default=False)
    is_processing = models.BooleanField(default=False)
    nocache = models.BooleanField(default=False)
    play_order = models.IntegerField(default=0)
    skip_asset_check = models.BooleanField(default=False)

    class Meta:
        db_table = 'assets'

    def __str__(self):
        return self.name

    def is_active(self):
        if self.is_enabled and self.start_date and self.end_date:
            current_time = timezone.now()
            return self.start_date < current_time < self.end_date

        return False


class ScheduleSlot(models.Model):
    """A time-of-day slot in the playback schedule.

    When at least one ScheduleSlot exists the viewer switches to
    "schedule mode": only assets linked via ScheduleSlotItem to the
    currently-active slot are played.  When the table is empty the
    viewer falls back to legacy behaviour (asset.is_active()).

    ``is_default=True`` marks the fallback slot whose content plays
    whenever no other slot covers the current time.  At most one
    default slot may exist.

    Overnight slots are supported: if ``time_from > time_to`` the
    window wraps past midnight (e.g. 22:00 -> 06:00).
    ``days_of_week`` refers to the **start** day of such a slot.
    """

    slot_id = models.TextField(
        primary_key=True,
        default=generate_asset_id,
        editable=False,
    )
    name = models.TextField(default='')
    slot_type = models.CharField(
        max_length=10,
        choices=SLOT_TYPE_CHOICES,
        default='time',
    )
    time_from = models.TimeField(default='00:00')
    time_to = models.TimeField(default='23:59')
    days_of_week = models.TextField(default=_default_all_days)
    is_default = models.BooleanField(default=False)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    no_loop = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)

    class Meta:
        db_table = 'schedule_slots'
        ordering = ['sort_order', 'time_from']

    def __str__(self):
        if self.is_default:
            return f'{self.name} (default)'
        if self.slot_type == 'event':
            return f'{self.name} (event @ {self.time_from})'
        return f'{self.name} {self.time_from}-{self.time_to}'

    def get_days_of_week(self):
        """Return days_of_week as a Python list of ints."""
        if isinstance(self.days_of_week, list):
            return self.days_of_week
        try:
            return json.loads(self.days_of_week)
        except (TypeError, json.JSONDecodeError):
            return [1, 2, 3, 4, 5, 6, 7]

    @property
    def is_overnight(self):
        """True when the slot wraps past midnight."""
        return self.time_from > self.time_to

    def is_currently_active(self):
        """Return True if this slot covers the current local time."""
        if self.is_default:
            return False

        now = timezone.localtime()
        current_time = now.time()
        current_weekday = now.isoweekday()

        if self.slot_type == 'event':
            return self._is_event_active(
                now,
                current_time,
                current_weekday,
            )

        days = self.get_days_of_week()

        if not self.is_overnight:
            return (
                current_weekday in days
                and self.time_from <= current_time < self.time_to
            )

        if current_time >= self.time_from:
            return current_weekday in days
        elif current_time < self.time_to:
            yesterday = current_weekday - 1 if current_weekday > 1 else 7
            return yesterday in days

        return False

    def _is_event_active(
        self,
        now,
        current_time,
        current_weekday,
    ):
        """Check if an event slot is currently active.

        Supports three recurrence modes:
        - One-time: start_date set, end_date null -> only that date.
        - Daily/recurring with range: start_date + end_date.
        - Weekly (selected days): days_of_week subset.
        """
        today = now.date()

        if self.start_date and self.end_date:
            if not (self.start_date <= today <= self.end_date):
                return False
        elif self.start_date:
            if today != self.start_date:
                return False
        elif self.end_date:
            if today > self.end_date:
                return False

        days = self.get_days_of_week()
        if days and current_weekday not in days:
            return False

        return self.time_from <= current_time < self.time_to


class ScheduleSlotItem(models.Model):
    """Links an Asset to a ScheduleSlot with optional duration override.

    The same asset may appear in multiple *different* slots but only
    once per slot (enforced by ``unique_together``).
    """

    item_id = models.TextField(
        primary_key=True,
        default=generate_asset_id,
        editable=False,
    )
    slot = models.ForeignKey(
        ScheduleSlot,
        on_delete=models.CASCADE,
        related_name='items',
    )
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='slot_items',
    )
    sort_order = models.IntegerField(default=0)
    duration_override = models.BigIntegerField(
        blank=True,
        null=True,
        help_text=('If set, overrides the asset duration for this slot.'),
    )

    class Meta:
        db_table = 'schedule_slot_items'
        ordering = ['sort_order']
        unique_together = [['slot', 'asset']]

    def __str__(self):
        return f'{self.slot.name} -> {self.asset.name}'

    @property
    def effective_duration(self):
        if self.duration_override is not None:
            return self.duration_override
        return self.asset.duration
