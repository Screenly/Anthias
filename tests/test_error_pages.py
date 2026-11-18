"""Smoke coverage for the branded 4xx/5xx error templates.

Django only swaps in 400/403/404/500.html when DEBUG=False. The dev
test client runs with DEBUG=True by default, so a broken extends
chain, missing static reference, or context-processor-dependent tag
in 500.html would only surface during a real production outage.
These tests exercise the templates directly and via the prod handler
path.
"""

from __future__ import annotations

import errno
import logging
import types
from collections.abc import Callable
from importlib import import_module

import pytest
from django.conf import settings as django_settings
from django.db import OperationalError
from django.http import HttpRequest, HttpResponse
from django.template import TemplateSyntaxError
from django.template.loader import get_template
from django.test import Client, RequestFactory, override_settings
from django.urls import get_callable, path
from django.views.defaults import (
    bad_request,
    page_not_found,
    permission_denied,
    server_error,
)

from anthias_server.lib import error_pages

_ERROR_TEMPLATES = [
    '400.html',
    '403.html',
    '403_csrf.html',
    '404.html',
    '500.html',
]


@pytest.fixture
def rf() -> RequestFactory:
    return RequestFactory()


@pytest.mark.parametrize('name', _ERROR_TEMPLATES)
def test_error_template_renders_without_request_context(name: str) -> None:
    """500.html in particular renders with no RequestContext (no context
    processors, no `request`). All four must render with an empty
    context — that's the strongest pre-prod check we can write."""
    html = get_template(name).render({})
    assert 'auth-card' in html
    assert 'error-card' in html
    # The dashboard CTA goes through {% url %}, so a successful render
    # also proves the URL reverser is wired up.
    assert 'href="/"' in html or 'href="/dashboard' in html


@override_settings(DEBUG=False, ALLOWED_HOSTS=['*'])
def test_404_handler_uses_branded_template() -> None:
    """End-to-end: with DEBUG off, an unknown URL hits Django's
    default 404 handler, which loads our 404.html."""
    client = Client()
    response = client.get('/this-path-does-not-exist')
    assert response.status_code == 404
    body = response.content.decode()
    assert 'Page not found' in body
    assert 'auth-card' in body


@pytest.mark.parametrize(
    'view, expected_status',
    [
        (bad_request, 400),
        (permission_denied, 403),
        (page_not_found, 404),
    ],
)
def test_default_handler_returns_branded_body(
    rf: RequestFactory,
    view: Callable[..., HttpResponse],
    expected_status: int,
) -> None:
    """Django's default 4xx handlers render `<code>.html` from the
    project template path. This bypasses URL routing and goes straight
    at the handler — useful because page_not_found et al. take a
    mandatory `exception` argument that the test client can't supply."""
    request = rf.get('/anything')
    response = view(request, exception=Exception('test'))
    assert response.status_code == expected_status
    assert b'auth-card' in response.content


def test_500_handler_returns_branded_body(rf: RequestFactory) -> None:
    """500's signature differs (no `exception` kwarg) and the renderer
    skips context processors, so test it on its own."""
    request = rf.get('/anything')
    response = server_error(request)
    assert response.status_code == 500
    assert b'auth-card' in response.content
    assert b'500' in response.content


class TestResilientServerError:
    """The 500 handler on a device whose filesystem has given up.

    Django's stock handler catches only ``TemplateDoesNotExist``, so
    an ``OSError`` from the template read escapes and the client gets
    no response at all — the failure mode behind Sentry ANTHIAS-62,
    where 24 ``[Errno 24] Too many open files: '.../500.html'`` events
    outranked the 3 database-open failures that caused them.
    """

    def test_serves_the_branded_template_when_disk_is_healthy(
        self, rf: RequestFactory
    ) -> None:
        response = error_pages.server_error(rf.get('/anything'))
        assert response.status_code == 500
        assert b'auth-card' in response.content
        assert b'Something broke on our end' in response.content

    @pytest.mark.parametrize(
        'error',
        [
            OSError(errno.EMFILE, 'Too many open files'),
            OSError(errno.EIO, 'Input/output error'),
            # 117, the corrupted-ext4 errno that bricked a device in
            # ANTHIAS-Y. No stdlib name for it, hence the literal.
            OSError(117, 'Structure needs cleaning'),
            TemplateSyntaxError('broken extends chain'),
        ],
    )
    def test_falls_back_when_the_template_cannot_be_read(
        self,
        rf: RequestFactory,
        monkeypatch: pytest.MonkeyPatch,
        error: Exception,
    ) -> None:
        def explode(name: str, *args: object, **kwargs: object) -> None:
            raise error

        monkeypatch.setattr('django.template.loader.get_template', explode)

        response = error_pages.server_error(rf.get('/anything'))

        assert response.status_code == 500
        assert response['Content-Type'] == 'text/html; charset=utf-8'
        assert b'Something broke on our end' in response.content
        # The whole point: the fallback body is held in memory, so it
        # must not reach for anything the failed disk would serve.
        assert b'<link' not in response.content
        assert b'/static/' not in response.content

    def test_fallback_is_logged_as_a_warning_not_an_error(
        self,
        rf: RequestFactory,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Error-level would re-report the fallout of an exception the
        tracker already has, once per request — which is what buried
        the cause in ANTHIAS-62. Warning keeps it as a breadcrumb."""

        def explode(name: str, *args: object, **kwargs: object) -> None:
            raise OSError(errno.EMFILE, 'Too many open files')

        monkeypatch.setattr('django.template.loader.get_template', explode)

        with caplog.at_level(logging.DEBUG, logger=error_pages.__name__):
            error_pages.server_error(rf.get('/anything'))

        records = [r for r in caplog.records if r.name == error_pages.__name__]
        assert [r.levelno for r in records] == [logging.WARNING]
        assert records[0].exc_info is not None

    def test_urlconf_points_at_this_handler(self) -> None:
        """A handler nobody routes to fixes nothing. ``handler500`` is
        read off the root URLconf by name at response time, so a typo
        or a move would only surface during a real outage."""
        urlconf = import_module(django_settings.ROOT_URLCONF)

        assert get_callable(urlconf.handler500) is error_pages.server_error

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['*'])
    def test_client_gets_a_page_when_the_view_and_the_template_fail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end through Django's exception middleware: a view
        raises, and the error page can't be read either. Stock Django
        propagates the second OSError out of the handler; we answer
        500 with a body."""

        def explode(name: str, *args: object, **kwargs: object) -> None:
            raise OSError(errno.EMFILE, 'Too many open files')

        monkeypatch.setattr('django.template.loader.get_template', explode)

        def boom(request: HttpRequest) -> HttpResponse:
            raise OperationalError('unable to open database file')

        urlpatterns = [path('boom', boom)]
        module = types.ModuleType('anthias_test_boom_urlconf')
        module.urlpatterns = urlpatterns  # type: ignore[attr-defined]
        module.handler500 = (  # type: ignore[attr-defined]
            'anthias_server.lib.error_pages.server_error'
        )

        with override_settings(ROOT_URLCONF=module):
            client = Client(raise_request_exception=False)
            response = client.get('/boom')

        assert response.status_code == 500
        assert b'Something broke on our end' in response.content
