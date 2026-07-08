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

from typing import TYPE_CHECKING, Callable

from django.utils import timezone

from anthias_server.django_project.settings import resolve_time_zone

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse


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
