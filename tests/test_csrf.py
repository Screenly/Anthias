"""Regression coverage for ``SameHostOriginCsrfMiddleware`` (#2867).

The middleware relaxes Django's strict scheme+host Origin check on a
same-host fallback so a TLS-terminating proxy in front of Anthias
(Caddy sidecar, Cloudflare Tunnel, Tailscale Serve, …) doesn't 403
every form submit. Cross-host POSTs, distinct-port web origins, and
bad / missing tokens must still fail.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

# The whole point of this suite is exercising plain-HTTP Origin headers
# against an HTTP-served Anthias — that's the deployment shape the bug
# happens on. Sonar's S5332 would flag every ``HTTP_ORIGIN='http://...'``
# call site, so funnel through module-level constants with a single
# NOSONAR per literal.
_HTTP_SAME_HOST_ORIGIN = 'http://anthias.local'  # NOSONAR
_HTTP_CROSS_HOST_ORIGIN = 'http://attacker.example'  # NOSONAR
_HTTPS_SAME_HOST_ORIGIN = 'https://anthias.local'


def _seed_csrf_cookie(client: Client, host: str) -> str:
    """GET the home page so the middleware sets ``csrftoken`` on the
    client, then return the raw cookie value. Django's CSRF check
    accepts the unmasked secret directly as ``csrfmiddlewaretoken``,
    so callers can sidestep parsing the rendered form HTML — keeps
    these tests focused on Origin handling rather than template markup.
    """
    response = client.get(reverse('anthias_app:home'), HTTP_HOST=host)
    assert response.status_code == 200
    return client.cookies['csrftoken'].value


@pytest.mark.django_db
def test_same_host_http_origin_passes() -> None:
    client = Client(enforce_csrf_checks=True)
    token = _seed_csrf_cookie(client, 'anthias.local')
    response = client.post(
        reverse('anthias_app:assets_control', args=['next']),
        data={'csrfmiddlewaretoken': token},
        HTTP_HOST='anthias.local',
        HTTP_ORIGIN=_HTTP_SAME_HOST_ORIGIN,
    )
    assert response.status_code == 302


@pytest.mark.django_db
def test_same_host_https_origin_on_http_passes() -> None:
    """The user-reported case in #2867. Browser advertises an
    ``https://device`` Origin (TLS terminated at a proxy / HSTS
    leftover / browser HTTPS-First) while uvicorn sees plain HTTP.
    Stock Django would 403; the custom middleware must accept."""
    client = Client(enforce_csrf_checks=True)
    token = _seed_csrf_cookie(client, 'anthias.local')
    response = client.post(
        reverse('anthias_app:assets_control', args=['next']),
        data={'csrfmiddlewaretoken': token},
        HTTP_HOST='anthias.local',
        HTTP_ORIGIN=_HTTPS_SAME_HOST_ORIGIN,
    )
    assert response.status_code == 302


@pytest.mark.django_db
def test_host_with_port_origin_without_port_passes() -> None:
    """Common reverse-proxy shape: the upstream ``Host`` carries an
    explicit (often default) port — e.g. ``Host: anthias.local:443``
    — while the browser's ``Origin`` is ``https://anthias.local``
    with the default port elided. Same site from the user's view;
    the fallback must compare hostnames and tolerate the port drift
    because at least one side is on the scheme's default port."""
    client = Client(enforce_csrf_checks=True)
    token = _seed_csrf_cookie(client, 'anthias.local:443')
    response = client.post(
        reverse('anthias_app:assets_control', args=['next']),
        data={'csrfmiddlewaretoken': token},
        HTTP_HOST='anthias.local:443',
        HTTP_ORIGIN=_HTTPS_SAME_HOST_ORIGIN,
    )
    assert response.status_code == 302


@pytest.mark.django_db
def test_same_host_distinct_non_default_ports_rejected() -> None:
    """``Origin: http://anthias.local:8080`` posting to a server on
    ``Host: anthias.local:8000`` is a cross-origin request even
    though the host matches — different non-default ports are
    distinct web origins. The fallback must keep rejecting this."""
    client = Client(enforce_csrf_checks=True)
    token = _seed_csrf_cookie(client, 'anthias.local:8000')
    response = client.post(
        reverse('anthias_app:assets_control', args=['next']),
        data={'csrfmiddlewaretoken': token},
        HTTP_HOST='anthias.local:8000',
        HTTP_ORIGIN='http://anthias.local:8080',  # NOSONAR
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_cross_host_origin_still_rejected() -> None:
    client = Client(enforce_csrf_checks=True)
    token = _seed_csrf_cookie(client, 'anthias.local')
    response = client.post(
        reverse('anthias_app:assets_control', args=['next']),
        data={'csrfmiddlewaretoken': token},
        HTTP_HOST='anthias.local',
        HTTP_ORIGIN=_HTTP_CROSS_HOST_ORIGIN,
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_missing_token_still_rejected() -> None:
    """The same-host Origin relaxation must not bypass the token
    check itself — a POST with a matching Origin but no
    ``csrfmiddlewaretoken`` body / ``X-CSRFToken`` header still 403s."""
    client = Client(enforce_csrf_checks=True)
    _seed_csrf_cookie(client, 'anthias.local')
    response = client.post(
        reverse('anthias_app:assets_control', args=['next']),
        HTTP_HOST='anthias.local',
        HTTP_ORIGIN=_HTTPS_SAME_HOST_ORIGIN,
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_bad_token_still_rejected() -> None:
    client = Client(enforce_csrf_checks=True)
    _seed_csrf_cookie(client, 'anthias.local')
    response = client.post(
        reverse('anthias_app:assets_control', args=['next']),
        data={'csrfmiddlewaretoken': 'not-the-real-token-12345678'},
        HTTP_HOST='anthias.local',
        HTTP_ORIGIN=_HTTPS_SAME_HOST_ORIGIN,
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_no_origin_header_still_works() -> None:
    """Curl-style clients with no Origin header (legitimate
    server-to-server callers, scripted operators) must still POST
    successfully when they carry a matching token + cookie."""
    client = Client(enforce_csrf_checks=True)
    token = _seed_csrf_cookie(client, 'anthias.local')
    response = client.post(
        reverse('anthias_app:assets_control', args=['next']),
        data={'csrfmiddlewaretoken': token},
        HTTP_HOST='anthias.local',
    )
    assert response.status_code == 302
