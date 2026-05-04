"""Smoke + redirect-safety coverage for the login view.

test_template_views.py explicitly skips login (line 4 of its module
docstring), and test_auth.py covers the auth backends in isolation,
not the surrounding view. So missing static refs / template-block
regressions on login.html and the `next` round-trip would not be
caught in CI without these tests.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest
from django.test import Client


@pytest.fixture
def client() -> Client:
    return Client()


# Centralized test fixtures so Sonar's S2068 (hardcoded credential)
# fires on a single suppressed line instead of once per assertion.
# These are arbitrary strings consumed only by a MagicMock auth
# backend below — they never reach a real credential store.
_FIXTURE_PASSWORD = 's3cret'  # NOSONAR
_FIXTURE_BAD_PASSWORD = 'wrong'  # NOSONAR


@pytest.mark.django_db
def test_login_get_renders_auth_card(client: Client) -> None:
    """A bare GET /login/ must produce the new auth-card layout. The
    string assertions here would catch a broken {% extends %}, missing
    static reference, or accidentally-removed form input — none of
    which would surface in test_auth.py's backend-only tests."""
    response = client.get('/login/')
    assert response.status_code == 200
    body = response.content.decode()
    assert 'auth-card' in body
    assert 'Welcome back' in body
    assert 'name="username"' in body
    assert 'name="password"' in body
    # The hidden `next` input must round-trip even when the request
    # didn't supply one (default to the dashboard).
    assert 'name="next"' in body


@pytest.mark.django_db
def test_login_get_round_trips_next(client: Client) -> None:
    """?next=/settings/ must round-trip into the form so the operator's
    original destination survives the POST."""
    response = client.get('/login/?next=/settings/')
    assert response.status_code == 200
    body = response.content.decode()
    assert 'value="/settings/"' in body


def _login_settings_with_check(check_result: bool) -> Any:
    """Minimal stand-in for the global `settings` module the login
    view reaches for. The view only inspects `settings.auth._check`,
    so a MagicMock with that attribute is enough."""
    fake = mock.MagicMock()
    fake.auth._check.return_value = check_result
    return fake


@pytest.mark.django_db
def test_login_post_honors_safe_next() -> None:
    """A successful POST with a same-host `next` redirects there, not
    to the dashboard — that's the bug Copilot flagged on the previous
    review pass."""
    fake_settings = _login_settings_with_check(True)
    with mock.patch('anthias_server.app.views.settings', fake_settings):
        client = Client()
        response = client.post(
            '/login/',
            {
                'username': 'alice',
                'password': _FIXTURE_PASSWORD,
                'next': '/settings/',
            },
        )
    assert response.status_code == 302
    assert response['Location'] == '/settings/'


@pytest.mark.django_db
def test_login_post_rejects_offhost_next() -> None:
    """Open-redirect guard: an attacker-controlled host in `next` must
    NOT be honoured — the view falls back to the dashboard. Without
    this filter, /login/?next=https://evil.example/ would let a phisher
    chain a real Anthias auth into a redirect to their page."""
    fake_settings = _login_settings_with_check(True)
    with mock.patch('anthias_server.app.views.settings', fake_settings):
        client = Client()
        response = client.post(
            '/login/',
            {
                'username': 'alice',
                'password': _FIXTURE_PASSWORD,
                'next': 'https://evil.example/',
            },
        )
    assert response.status_code == 302
    # url_has_allowed_host_and_scheme rejects off-host targets, so
    # _safe_login_next falls back to anthias_app:home (which reverses
    # to '/').
    assert response['Location'] == '/'


@pytest.mark.django_db
def test_login_post_invalid_creds_keeps_next() -> None:
    """A failed login re-renders the form, and the next value must
    survive so the operator's destination isn't lost on a typo."""
    fake_settings = _login_settings_with_check(False)
    with mock.patch('anthias_server.app.views.settings', fake_settings):
        client = Client()
        response = client.post(
            '/login/',
            {
                'username': 'alice',
                'password': _FIXTURE_BAD_PASSWORD,
                'next': '/settings/',
            },
        )
    assert response.status_code == 200
    body = response.content.decode()
    assert 'value="/settings/"' in body
    assert 'Invalid username or password' in body
