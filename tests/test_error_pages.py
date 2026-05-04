"""Smoke coverage for the branded 4xx/5xx error templates.

Django only swaps in 400/403/404/500.html when DEBUG=False. The dev
test client runs with DEBUG=True by default, so a broken extends
chain, missing static reference, or context-processor-dependent tag
in 500.html would only surface during a real production outage.
These tests exercise the templates directly and via the prod handler
path.
"""

from __future__ import annotations

from typing import Callable

import pytest
from django.http import HttpResponse
from django.template.loader import get_template
from django.test import Client, RequestFactory, override_settings
from django.views.defaults import (
    bad_request,
    page_not_found,
    permission_denied,
    server_error,
)

_ERROR_TEMPLATES = ['400.html', '403.html', '404.html', '500.html']


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
