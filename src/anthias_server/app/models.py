import json
import uuid
from datetime import datetime
from typing import Any

from django.db import models
from django.utils import timezone


ALL_DAYS = [1, 2, 3, 4, 5, 6, 7]

# Upper bound for ``Asset.metadata['refresh_interval_s']`` (seconds).
# 24h cap acts as a typo guard — anything beyond is almost certainly
# a units mistake — and is a hostile-input guard for the int math
# in the C++ webview's setReloadInterval (``seconds * 1000`` would
# otherwise overflow). Imported by the v2 serializer (write
# validation), the form handler (clamping), and mirrored by
# kMaxReloadIntervalS in src/anthias_webview/src/view.cpp.
REFRESH_INTERVAL_S_MAX = 86400


def clamp_refresh_interval(value: Any) -> int:
    """Coerce an arbitrary ``metadata['refresh_interval_s']`` value to
    a safe int in ``[0, REFRESH_INTERVAL_S_MAX]``.

    The serializer's write path rejects out-of-range values, but a
    hand-edited row, a legacy import, or a non-int JSON value could
    leave junk in the column. Every read site (v2 serializer, edit-
    modal ``to_json`` filter, viewer ``asset_loop``, page-form
    handler) funnels through this so the clamp can't drift between
    them. ``Any`` rather than ``object`` because callers pass dict /
    list / unknown JSON values and we want ``int(value)`` to attempt
    coercion regardless — TypeError / ValueError gets caught.
    """
    try:
        interval = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(interval, REFRESH_INTERVAL_S_MAX))


def repair_mojibake(text: str | None) -> str | None:
    """Undo the classic ``UTF-8 bytes decoded as Latin-1`` mojibake.

    A misbehaving uploader that double-encodes a filename turns
    ``Formulários`` into ``FormulÃ¡rios`` (the UTF-8 bytes ``\\xc3\\xa1``
    of ``á`` read back as the two Latin-1 chars ``Ã`` + ``¡``). Anthias
    itself never does this — Django's multipart parser and DRF both
    decode as UTF-8 — but the corrupted text arrives already mangled in
    the request body and we would otherwise store it verbatim, so the
    operator sees garbled asset names in the UI and in the viewer's
    ``Showing asset …`` log line.

    The repair is deliberately conservative and deterministic: it only
    fires when *every* character is in the Latin-1 range (so
    ``encode('latin-1')`` round-trips) **and** those bytes form a valid
    UTF-8 string, which is then returned only if it actually differs from
    the input. That is a strong heuristic for double-encoded UTF-8, but
    not a proof: a name that is *genuinely* Latin-1 yet whose bytes also
    happen to be valid UTF-8 (e.g. ``Â©`` → ``©``) is indistinguishable
    from mojibake and gets rewritten too. Such collisions are vanishingly
    rare in real asset filenames, and the alternative — leaving every
    ``FormulÃ¡rios`` garbled — is worse, so we accept the trade-off.
    Correctly-stored ``Formulários``, ``Café``, or ``日本語`` raise on the
    encode or decode step and are returned untouched. Idempotent:
    re-running on already-repaired text is a no-op.
    """
    if not text:
        return text
    try:
        repaired = text.encode('latin-1').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return repaired if repaired != text else text


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
    # Per-asset bag of processing-pipeline state. Carries flags written
    # by the upload-time normalisation tasks (normalize_image_asset,
    # normalize_video_asset) — original file extension, whether a
    # transcode happened, the last processing error if any — without
    # widening the schema for each new field. The pipeline writes; the
    # model itself never reads/branches on it. Default ``dict`` (not
    # None) so callers can ``asset.metadata['k'] = v`` without an
    # ``or {}`` guard.
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'assets'

    def __str__(self) -> str:
        return str(self.name)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Repair double-encoded UTF-8 in ``name`` before persisting.

        A single write-side chokepoint so every create/update path —
        the web form, all four API versions, and the legacy Screenly
        import — stores a clean name regardless of an upstream client
        that double-encoded the filename. See ``repair_mojibake`` for
        why this is safe (no-op on correctly-encoded text). The repair
        is cheap and idempotent, so running it on every save (including
        reachability/processing-flag updates that leave ``name``
        unchanged) costs nothing.
        """
        self.name = repair_mojibake(self.name)
        super().save(*args, **kwargs)

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
