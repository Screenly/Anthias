"""Regression coverage for ``SameHostOriginCsrfMiddleware`` (#2867).

The middleware relaxes Django's strict scheme+host Origin check on a
same-host fallback so a TLS-terminating proxy in front of Anthias
(Caddy sidecar, Cloudflare Tunnel, Tailscale Serve, …) doesn't 403
every form submit. Cross-host POSTs and bad/missing tokens must
still fail.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse


def _seed_form_token(client: Client, host: str) -> str:
    """Render the home page so the middleware sets ``csrftoken`` on
    ``client.cookies`` and emits a matching token in the form. Return
    the form's masked token."""
    import re

    response = client.get(reverse('anthias_app:home'), HTTP_HOST=host)
    assert response.status_code == 200
    match = re.search(
        r'name="csrfmiddlewaretoken"\s+value="([^"]+)"',
        response.content.decode(),
    )
    assert match is not None, 'no csrf token rendered in home form'
    return match.group(1)


@pytest.mark.django_db
def test_same_host_http_origin_passes() -> None:
    client = Client(enforce_csrf_checks=True)
    token = _seed_form_token(client, 'anthias.local')
    response = client.post(
        reverse('anthias_app:assets_control', args=['next']),
        data={'csrfmiddlewaretoken': token},
        HTTP_HOST='anthias.local',
        HTTP_ORIGIN='http://anthias.local',
    )
    assert response.status_code == 302


@pytest.mark.django_db
def test_same_host_https_origin_on_http_passes() -> None:
    """The user-reported case in #2867. Browser advertises an
    ``https://device`` Origin (TLS terminated at a proxy / HSTS
    leftover / browser HTTPS-First) while uvicorn sees plain HTTP.
    Stock Django would 403; the custom middleware must accept."""
    client = Client(enforce_csrf_checks=True)
    token = _seed_form_token(client, 'anthias.local')
    response = client.post(
        reverse('anthias_app:assets_control', args=['next']),
        data={'csrfmiddlewaretoken': token},
        HTTP_HOST='anthias.local',
        HTTP_ORIGIN='https://anthias.local',
    )
    assert response.status_code == 302


@pytest.mark.django_db
def test_cross_host_origin_still_rejected() -> None:
    client = Client(enforce_csrf_checks=True)
    token = _seed_form_token(client, 'anthias.local')
    response = client.post(
        reverse('anthias_app:assets_control', args=['next']),
        data={'csrfmiddlewaretoken': token},
        HTTP_HOST='anthias.local',
        HTTP_ORIGIN='http://attacker.example',
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_missing_token_still_rejected() -> None:
    """The same-host Origin relaxation must not bypass the token
    check itself — a POST with a matching Origin but no
    ``csrfmiddlewaretoken`` body / ``X-CSRFToken`` header still 403s."""
    client = Client(enforce_csrf_checks=True)
    _seed_form_token(client, 'anthias.local')
    response = client.post(
        reverse('anthias_app:assets_control', args=['next']),
        HTTP_HOST='anthias.local',
        HTTP_ORIGIN='https://anthias.local',
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_bad_token_still_rejected() -> None:
    client = Client(enforce_csrf_checks=True)
    _seed_form_token(client, 'anthias.local')
    response = client.post(
        reverse('anthias_app:assets_control', args=['next']),
        data={'csrfmiddlewaretoken': 'not-the-real-token-12345678'},
        HTTP_HOST='anthias.local',
        HTTP_ORIGIN='https://anthias.local',
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_no_origin_header_still_works() -> None:
    """Curl-style clients with no Origin header (legitimate
    server-to-server callers, scripted operators) must still POST
    successfully when they carry a matching token + cookie."""
    client = Client(enforce_csrf_checks=True)
    token = _seed_form_token(client, 'anthias.local')
    response = client.post(
        reverse('anthias_app:assets_control', args=['next']),
        data={'csrfmiddlewaretoken': token},
        HTTP_HOST='anthias.local',
    )
    assert response.status_code == 302
