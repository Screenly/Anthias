import json
import uuid
from datetime import datetime

from django.db import models
from django.utils import timezone


ALL_DAYS = [1, 2, 3, 4, 5, 6, 7]


def generate_asset_id() -> str:
    return uuid.uuid4().hex


def _default_play_days() -> str:
    return json.dumps(ALL_DAYS)


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
    play_days = models.TextField(default=_default_play_days)
    play_time_from = models.TimeField(blank=True, null=True)
    play_time_to = models.TimeField(blank=True, null=True)
    is_reachable = models.BooleanField(default=True)
    last_reachability_check = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'assets'

    def __str__(self) -> str:
        return str(self.name)

    def get_play_days(self) -> list[int]:
        """Parse play_days into a sorted, deduped list of ints 1-7.

        Falls back to all days if the value is missing, malformed JSON,
        not a list, empty, or contains anything outside the 1-7 range.
        The API validates on write, but admin / direct DB edits could
        otherwise leave a row with junk in this column. Normalising on
        read also keeps API responses consistent (sorted, no dupes).
        """
        if isinstance(self.play_days, list):
            value = self.play_days
        else:
            try:
                value = json.loads(self.play_days)
            except (TypeError, json.JSONDecodeError):
                return list(ALL_DAYS)

        if not isinstance(value, list):
            return list(ALL_DAYS)
        if not all(isinstance(d, int) and 1 <= d <= 7 for d in value):
            return list(ALL_DAYS)

        deduped = sorted(set(value))
        if not deduped:
            return list(ALL_DAYS)
        return deduped

    def has_window_filter(self) -> bool:
        """True if this asset has any day-of-week or time-of-day filter set.

        A time-of-day filter only applies when both endpoints are set —
        _matches_play_window() treats a partial window as no filter — so
        report it that way here too. Otherwise a stray single-endpoint
        value (rejected by the v2 API but possible via admin / direct DB
        edits) would force the windowed deadline cap on every tick
        without actually filtering anything.
        """
        if self.play_time_from is not None and self.play_time_to is not None:
            return True
        return self.get_play_days() != list(ALL_DAYS)

    def is_active(self, now: datetime | None = None) -> bool:
        if not (self.is_enabled and self.start_date and self.end_date):
            return False
        if now is None:
            now = timezone.now()
        if not (self.start_date < now < self.end_date):
            return False
        return self._matches_play_window(timezone.localtime(now))

    def _matches_play_window(self, now_local: datetime) -> bool:
        """Day-of-week and time-of-day filter, evaluated in local time.

        Overnight windows (play_time_from > play_time_to) wrap past
        midnight; play_days refers to the **start** day of such a
        window. With no window fields set this is a no-op (returns
        True), so unscheduled assets behave as before.
        """
        weekday = now_local.isoweekday()
        days = self.get_play_days()

        if self.play_time_from is None or self.play_time_to is None:
            return weekday in days

        current_time = now_local.time()

        if self.play_time_from <= self.play_time_to:
            if weekday not in days:
                return False
            return self.play_time_from <= current_time < self.play_time_to

        # Overnight: window is [play_time_from, 24:00) on day D plus
        # [00:00, play_time_to) on day D+1. play_days lists the D side.
        if current_time >= self.play_time_from:
            return weekday in days
        if current_time < self.play_time_to:
            yesterday = weekday - 1 if weekday > 1 else 7
            return yesterday in days
        return False
