import json
import re
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


# Upper bound for ``Asset.duration`` (seconds). The hard constraint is
# the viewer: ``asset_loop`` / ``view_video`` feed the value straight
# into ``threading.Event.wait``, and a timeout past C ``PyTime_t``
# range (int64 nanoseconds, ~9.2e9 s ≈ 292 years) raises
# OverflowError, crash-looping the viewer
# (Sentry ANTHIAS-3E — an operator typed 9999999999999 to mean
# "forever" and took the screen down). One year is effectively
# "pinned forever" for signage while staying a typo guard. Enforced
# by the v2 serializers + settings (write validation), the
# v1/v1.1/v1.2 create paths, the page-form handlers (clamping), and
# the viewer's read-side clamp.
DURATION_S_MAX = 365 * 24 * 60 * 60


# Per-asset custom HTTP request headers for webpage assets (feature
# #2215). Stored in ``Asset.metadata['headers']`` as a ``{name: value}``
# object and injected by the C++ webview's request interceptor on
# same-origin requests (scheme+host+port), so a private dashboard (e.g. a
# Grafana service-account token) renders without having to be made
# public. Bounds keep a hostile or typo'd row from bloating the D-Bus
# payload, the DB blob, or a single request's header block. This
# server-side validation is the primary gate keeping CR/LF out of the
# wire (header/response-splitting); the webview re-validates defensively
# at its D-Bus boundary too. ``MAX_HEADER_VALUE_LEN`` is a byte cap
# (values go on the wire as UTF-8), matching the webview's check.
MAX_ASSET_HEADERS = 20
MAX_HEADER_NAME_LEN = 256
MAX_HEADER_VALUE_LEN = 4096

# RFC 7230 ``field-name`` is ``1*tchar``. Anchored so a name carrying a
# colon, whitespace, or a control char is rejected outright rather than
# smuggled onto the wire.
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def _is_valid_header_name(name: str) -> bool:
    return len(name) <= MAX_HEADER_NAME_LEN and bool(
        _HEADER_NAME_RE.match(name)
    )


def _is_valid_header_value(value: str) -> bool:
    # CR / LF / NUL would let a stored value inject additional headers
    # (or split the request) once the C++ side writes it verbatim, so
    # they are rejected here — the one place every write path funnels
    # through. Everything else reaches the origin byte-for-byte.
    if any(ch in value for ch in ('\r', '\n', '\x00')):
        return False
    # Cap the UTF-8 *byte* length, not the character count: the value is
    # sent on the wire as UTF-8 (the webview's toUtf8()), so a string of
    # multi-byte characters would otherwise exceed the intended byte
    # budget. Keeps this in lockstep with the webview's byte-based cap.
    return len(value.encode('utf-8')) <= MAX_HEADER_VALUE_LEN


def normalize_asset_headers(value: Any) -> dict[str, str]:
    """Coerce an arbitrary ``metadata['headers']`` value into a safe
    ``{name: value}`` dict, dropping (not raising on) anything malformed.

    Same defensive posture as ``clamp_refresh_interval``: the strict
    reject-on-invalid path lives in the v2 serializer's write validation
    (``validate_asset_headers`` below), but a hand-edited row, a legacy
    import, or a non-string JSON value must never crash the viewer read
    path or the API GET. ``Any`` because callers pass whatever JSON the
    column happens to hold.
    """
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        if len(out) >= MAX_ASSET_HEADERS:
            break
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            continue
        name = raw_name.strip()
        if not _is_valid_header_name(name):
            continue
        if not _is_valid_header_value(raw_value):
            continue
        out[name] = raw_value
    return out


def validate_asset_headers(value: Any) -> dict[str, str]:
    """Strict counterpart to ``normalize_asset_headers``: raises
    ``ValueError`` (with a human reason) on any malformed entry instead
    of silently dropping it.

    Used by the v2 API write path so an operator sending a bad header
    gets a 400 that names the problem, rather than a 200 that quietly
    discarded half of what they typed. The server-rendered form uses the
    forgiving ``parse_header_lines`` path instead (mirroring how
    ``refresh_interval_s`` is 400'd by the API but clamped by the form).
    """
    if not isinstance(value, dict):
        raise ValueError('Headers must be an object of name/value pairs.')
    if len(value) > MAX_ASSET_HEADERS:
        raise ValueError(
            f'At most {MAX_ASSET_HEADERS} custom headers are allowed.'
        )
    out: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        name = raw_name.strip() if isinstance(raw_name, str) else ''
        if not _is_valid_header_name(name):
            raise ValueError(f'Invalid header name: {raw_name!r}')
        if not isinstance(raw_value, str) or not _is_valid_header_value(
            raw_value
        ):
            raise ValueError(f'Invalid value for header {name!r}')
        out[name] = raw_value
    return out


def parse_header_lines(text: Any) -> dict[str, str]:
    """Parse a textarea of ``Name: Value`` lines into a sanitised header
    dict for the server-rendered edit form.

    Blank lines and lines without a colon are ignored; the value keeps
    everything after the first colon (so ``Bearer a:b`` survives). The
    result is funnelled through ``normalize_asset_headers`` so the form
    clamps (drops bad entries) rather than 400ing, matching the
    ``refresh_interval_s`` form contract.
    """
    if not isinstance(text, str):
        return {}
    headers: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ':' not in line:
            continue
        name, _, raw_value = line.partition(':')
        headers[name.strip()] = raw_value.strip()
    return normalize_asset_headers(headers)


def clamp_duration(value: Any) -> int:
    """Coerce an arbitrary ``Asset.duration`` value to a safe int in
    ``[0, DURATION_S_MAX]``.

    The API write paths reject out-of-range values, but a hand-edited
    row or a legacy import can still hold junk, and the viewer must
    never crash on a DB value. Same contract as
    ``clamp_refresh_interval`` below: garbage coerces to 0.
    """
    try:
        duration = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(duration, DURATION_S_MAX))


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
    # Per-asset opt-out of TLS certificate verification for a remote
    # HTTPS URI (e.g. media served from an intranet host with a
    # self-signed / untrusted-CA cert). Composes with the device-wide
    # ``verify_ssl`` setting: verification is skipped when the global
    # setting is off OR this flag is set. Only ever loosens, never
    # tightens. Consulted by the reachability probe (url_fails) and,
    # for images/web pages, by the C++ webview per load.
    skip_ssl_verify = models.BooleanField(default=False)
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
