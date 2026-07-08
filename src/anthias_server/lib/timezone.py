"""Per-request activation of the operator-selected timezone.

``TIME_ZONE`` in Django settings is resolved once at process start, so
on its own an operator changing the timezone in Settings would not take
effect until the next restart. This middleware re-resolves the
effective zone (config -> TZ env -> host -> UTC) on every request and
activates it for the duration of that request, so a save is reflected
immediately in every rendered template, the REST API, and — crucially —
the server-evaluated ``ViewerPlaylistViewV2`` that feeds the C++ viewer
its local play-window decisions.

The read is a small config-file parse; kept fresh on purpose so the
change is live. Any failure deactivates back to the process default
(``TIME_ZONE``) rather than 500-ing the request.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Callable

from django.utils import timezone

from anthias_server.django_project.settings import resolve_time_zone

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse


def format_utc_offset(dt: datetime) -> str:
    """Render a datetime's UTC offset as ``UTC+HH:MM`` / ``UTC-HH:MM``.

    ``UTC`` alone for a naive value (``%z`` yields an empty string).
    Shared by the System Info page and the ``/api/v2/info`` clock so
    the two never drift.
    """
    raw = dt.strftime('%z')  # e.g. '+0200', '' for a naive value
    if len(raw) < 5:
        return 'UTC'
    return f'UTC{raw[:3]}:{raw[3:]}'


class TimezoneActivationMiddleware:
    def __init__(
        self, get_response: Callable[[HttpRequest], HttpResponse]
    ) -> None:
        self.get_response = get_response

    def __call__(self, request: 'HttpRequest') -> 'HttpResponse':
        try:
            timezone.activate(resolve_time_zone())
        except Exception:
            # A bad/removed zone must never take the whole request down;
            # fall back to the process default (settings.TIME_ZONE).
            timezone.deactivate()
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
