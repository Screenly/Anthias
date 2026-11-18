"""A 500 page that survives whatever caused the 500.

Django's ``django.views.defaults.server_error`` loads ``500.html``
off disk and catches only ``TemplateDoesNotExist``. On a device whose
storage or file-descriptor budget has already failed — which is
precisely the condition that produced the original 500 — that load
raises an ``OSError`` of its own. Nothing catches it: it escapes
``handle_uncaught_exception``, uvicorn drops the connection, and the
operator is left staring at a browser error rather than a page that
tells them their card is dying.

Observed in the field on a Pi 5 that ran out of file descriptors:
three ``OperationalError: unable to open database file`` events — the
real fault, Sentry ANTHIAS-63 — buried under twenty-four
``OSError: [Errno 24] Too many open files:
'.../app/templates/500.html'`` from the error page raised for them
(Sentry ANTHIAS-62). The cascade is worse than noise: the tracker
ranks by event count, so the symptom outranked its own cause 8:1.

So: try the branded template, and when the filesystem refuses, serve
a page that has been in memory since import. ``500.html`` extends
``_error.html``, which pulls a layout and a stylesheet link, so the
working path is several reads deep — all of which this covers.

Only the 500 handler is replaced. The 4xx handlers render the same
way and would fail the same way, but they are reached by ordinary
requests on healthy devices; this one is reached *because* something
is already broken, which is what makes a disk read the wrong thing
to depend on here.
"""

import logging

from django.http import HttpRequest, HttpResponse, HttpResponseServerError
from django.template import loader
from django.views.decorators.csrf import requires_csrf_token

logger = logging.getLogger(__name__)

ERROR_500_TEMPLATE_NAME = '500.html'

# Deliberately self-contained: no {% extends %}, no stylesheet link, no
# image. Every one of those is a file read, and a file read is the
# thing that has just failed. Inline CSS keeps it recognisably Anthias
# without reaching for /static.
FALLBACK_500_BODY = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Server error &mdash; Anthias</title>
<style>
body{margin:0;min-height:100vh;display:flex;align-items:center;
justify-content:center;background:#16181d;color:#e7e9ee;
font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:34rem;padding:2rem;text-align:center}
h1{font-size:1.5rem;margin:0 0 .75rem}
p{line-height:1.6;color:#aeb4c2;margin:0 0 .75rem}
code{color:#e7e9ee}
</style>
</head>
<body>
<main>
<h1>Something broke on our end</h1>
<p>The server hit an unexpected error, and could not read its own
error page from disk &mdash; usually a sign that this device's
storage is failing or full.</p>
<p>Check the device logs (<code>docker logs anthias-server</code>),
and the free space and health of the SD card.</p>
</main>
</body>
</html>
"""


@requires_csrf_token
def server_error(
    request: HttpRequest,
    template_name: str = ERROR_500_TEMPLATE_NAME,
) -> HttpResponse:
    """Render ``500.html``, falling back to an in-memory page.

    ``@requires_csrf_token`` matches Django's own handler: the error
    page is rendered from a request whose CSRF processing may never
    have run, and without it ``{% csrf_token %}`` anywhere in the
    template chain would raise instead of render.
    """
    try:
        html = loader.get_template(template_name).render()
    except Exception:
        # Intentionally everything. This is the last handler between
        # the failure and the socket, so any exception it lets through
        # costs the operator the response entirely. Warning, not
        # error: the exception that got us here has already been
        # reported, and re-reporting its fallout once per request is
        # what buried the cause in the first place. This still lands
        # as a breadcrumb on the next event.
        logger.warning(
            'Could not render %s; serving the built-in error page. '
            'The device storage likely needs attention.',
            template_name,
            exc_info=True,
        )
        return HttpResponseServerError(
            FALLBACK_500_BODY,
            content_type='text/html; charset=utf-8',
        )
    return HttpResponseServerError(html)
