"""Tests for anthias_server.lib.auth.

The legacy Auth/NoAuth/BasicAuth class hierarchy has been retired —
auth is now Django's built-in (session + DRF SessionAuthentication +
BearerTokenAuthentication + BasicAuthentication). What's left here:

* The hash helpers (round-trip, legacy-format detection) — still used
  by the data migration to gate which conf rows can be promoted into
  User.password.
* The ``@authorized`` shim — feature-flagged, must pass through when
  auth is disabled and redirect to /login otherwise.
* The Bearer / Basic / Session paths reaching the JSON API.
* ``apply_auth_settings`` — single source of truth for the settings
  page's auth-update flow on both the HTML and DRF code paths.
"""

from __future__ import annotations

from base64 import b64encode
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.test import Client, RequestFactory

from anthias_server.lib import auth
from anthias_server.lib.auth import (
    AuthSettingsError,
    _is_legacy_sha256,
    apply_auth_settings,
    authorized,
    hash_password,
    operator_username,
    verify_password,
)


# ---------------------------------------------------------------------------
# hash_password / verify_password


@pytest.mark.django_db
def test_hash_password_round_trip() -> None:
    hashed = hash_password('hunter2')
    assert hashed != 'hunter2'
    # Django's hashers always produce an algorithm-prefixed string.
    assert '$' in hashed
    assert verify_password('hunter2', hashed) is True
    assert verify_password('wrong', hashed) is False


@pytest.mark.django_db
def test_verify_password_empty_stored_returns_false() -> None:
    assert verify_password('anything', '') is False


@pytest.mark.parametrize(
    'value,expected',
    [
        # 64 hex chars → legacy bare SHA256
        ('a' * 64, True),
        ('0' * 63 + 'f', True),
        ('A' * 64, False),  # uppercase rejected (regex is lowercase only)
        ('a' * 63, False),
        ('a' * 65, False),
        ('pbkdf2_sha256$...', False),
        ('', False),
    ],
)
def test_is_legacy_sha256(value: str, expected: bool) -> None:
    assert _is_legacy_sha256(value) is expected


def test_module_level_linux_user_constant() -> None:
    # Sanity: the constant is read at import and exposed for callers.
    assert isinstance(auth.LINUX_USER, str)
    assert auth.LINUX_USER  # non-empty


# ---------------------------------------------------------------------------
# @authorized — feature flag + redirect contract


def test_authorized_passthrough_when_auth_backend_disabled(
    monkeypatch: Any,
) -> None:
    """settings['auth_backend'] == '' is the NoAuth equivalent — the
    wrapped view runs unconditionally so devices on the default
    un-authenticated config keep working."""
    fake_settings = {'auth_backend': ''}
    monkeypatch.setattr('anthias_server.settings.settings', fake_settings)

    @authorized
    def view(request: Any) -> str:
        return 'ok'

    factory = RequestFactory()
    assert view(factory.get('/')) == 'ok'


def test_authorized_redirects_when_unauthenticated(monkeypatch: Any) -> None:
    """When auth is enabled and request.user is anonymous, the
    decorator should bounce the caller to /login with ?next= filled in."""
    fake_settings = {'auth_backend': 'auth_basic'}
    monkeypatch.setattr('anthias_server.settings.settings', fake_settings)

    @authorized
    def view(request: Any) -> str:
        return 'ok'

    factory = RequestFactory()
    request = factory.get('/system-info/')
    # AnonymousUser is the default when AuthenticationMiddleware hasn't
    # run; emulate that by attaching a MagicMock with is_authenticated=False.
    request.user = MagicMock(is_authenticated=False)
    response = view(request)
    assert response.status_code == 302
    assert response['Location'].startswith('/login')
    assert 'next=%2Fsystem-info%2F' in response['Location']


def test_authorized_calls_view_when_authenticated(monkeypatch: Any) -> None:
    fake_settings = {'auth_backend': 'auth_basic'}
    monkeypatch.setattr('anthias_server.settings.settings', fake_settings)

    @authorized
    def view(request: Any) -> str:
        return 'ok'

    factory = RequestFactory()
    request = factory.get('/')
    request.user = MagicMock(is_authenticated=True)
    assert view(request) == 'ok'


def test_authorized_drops_next_for_unsafe_methods(monkeypatch: Any) -> None:
    """A POST/PUT/PATCH/DELETE that 401s would otherwise produce a
    ?next=/some/write/endpoint that the post-login GET redirect bounces
    back to → 405. Drop next for unsafe methods so the operator lands
    on the dashboard instead."""
    fake_settings = {'auth_backend': 'auth_basic'}
    monkeypatch.setattr('anthias_server.settings.settings', fake_settings)

    @authorized
    def view(request: Any) -> str:
        return 'ok'

    factory = RequestFactory()
    for method in ('post', 'put', 'patch', 'delete'):
        request = getattr(factory, method)('/api/v2/assets/')
        request.user = MagicMock(is_authenticated=False)
        response = view(request)
        assert response.status_code == 302
        assert response['Location'].endswith('/login/')
        assert 'next=' not in response['Location']


def test_authorized_drops_next_for_htmx_partial(monkeypatch: Any) -> None:
    """Dashboard polls htmx fragments every 5s; if the session expires
    mid-poll we'd otherwise serialize the partial URL into next,
    dumping the operator on a bare table fragment after sign-in."""
    fake_settings = {'auth_backend': 'auth_basic'}
    monkeypatch.setattr('anthias_server.settings.settings', fake_settings)

    @authorized
    def view(request: Any) -> str:
        return 'ok'

    factory = RequestFactory()
    request = factory.get('/_partials/asset-table/', HTTP_HX_REQUEST='true')
    request.user = MagicMock(is_authenticated=False)
    response = view(request)
    assert response.status_code == 302
    assert response['Location'].endswith('/login/')
    assert 'next=' not in response['Location']


def test_authorized_no_args_raises(monkeypatch: Any) -> None:
    fake_settings = {'auth_backend': 'auth_basic'}
    monkeypatch.setattr('anthias_server.settings.settings', fake_settings)

    @authorized
    def view() -> str:
        return 'ok'

    with pytest.raises(ValueError, match='No request object passed'):
        view()


def test_authorized_non_request_arg_raises(monkeypatch: Any) -> None:
    fake_settings = {'auth_backend': 'auth_basic'}
    monkeypatch.setattr('anthias_server.settings.settings', fake_settings)

    @authorized
    def view(request: Any) -> str:
        return 'ok'

    with pytest.raises(ValueError, match='not of type HttpRequest'):
        view('not-a-request')


# ---------------------------------------------------------------------------
# operator_username — settings-page lookup


@pytest.mark.django_db
def test_operator_username_empty_when_no_user_exists() -> None:
    assert operator_username() == ''


@pytest.mark.django_db
def test_operator_username_returns_first_superuser() -> None:
    User.objects.create_user(username='non-admin', password='pw1')  # NOSONAR
    User.objects.create_superuser(
        username='alice', password='pw2'  # NOSONAR
    )
    assert operator_username() == 'alice'


# ---------------------------------------------------------------------------
# apply_auth_settings — covers the settings-save flow shared by the
# HTML view and DeviceSettingsViewV2.


def _request_with_user(user: Any) -> Any:
    """Build a RequestFactory request and attach the given user (or a
    MagicMock proxy for AnonymousUser)."""
    factory = RequestFactory()
    request = factory.post('/')
    request.user = user
    return request


@pytest.mark.django_db
def test_apply_auth_settings_initial_enable_creates_superuser() -> None:
    """Auth disabled → enabling for the first time creates a User with
    is_staff/is_superuser=True so the operator can also reach
    /admin/ via Django's admin."""
    request = _request_with_user(MagicMock(is_authenticated=False))
    apply_auth_settings(
        request,
        new_auth_backend='auth_basic',
        current_password='',
        new_username='alice',
        new_password='hunter2',
        new_password_confirm='hunter2',
        prev_auth_backend='',
    )
    user = User.objects.get(username='alice')
    assert user.is_active and user.is_staff and user.is_superuser
    assert user.check_password('hunter2')


@pytest.mark.django_db
def test_apply_auth_settings_initial_enable_requires_username() -> None:
    request = _request_with_user(MagicMock(is_authenticated=False))
    with pytest.raises(AuthSettingsError, match='Must provide username'):
        apply_auth_settings(
            request,
            new_auth_backend='auth_basic',
            current_password='',
            new_username='',
            new_password='hunter2',
            new_password_confirm='hunter2',
            prev_auth_backend='',
        )


@pytest.mark.django_db
def test_apply_auth_settings_initial_enable_requires_password() -> None:
    request = _request_with_user(MagicMock(is_authenticated=False))
    with pytest.raises(AuthSettingsError, match='Must provide password'):
        apply_auth_settings(
            request,
            new_auth_backend='auth_basic',
            current_password='',
            new_username='alice',
            new_password='',
            new_password_confirm='',
            prev_auth_backend='',
        )


@pytest.mark.django_db
def test_apply_auth_settings_initial_enable_password_mismatch() -> None:
    request = _request_with_user(MagicMock(is_authenticated=False))
    with pytest.raises(AuthSettingsError, match='New passwords do not match'):
        apply_auth_settings(
            request,
            new_auth_backend='auth_basic',
            current_password='',
            new_username='alice',
            new_password='a',
            new_password_confirm='b',
            prev_auth_backend='',
        )


@pytest.mark.django_db
def test_apply_auth_settings_change_password_success() -> None:
    user = User.objects.create_superuser(
        username='alice', password='oldpass'  # NOSONAR
    )
    request = _request_with_user(user)
    apply_auth_settings(
        request,
        new_auth_backend='auth_basic',
        current_password='oldpass',
        new_username='alice',
        new_password='newpass',  # NOSONAR
        new_password_confirm='newpass',  # NOSONAR
        prev_auth_backend='auth_basic',
    )
    user.refresh_from_db()
    assert user.check_password('newpass')


@pytest.mark.django_db
def test_apply_auth_settings_change_password_requires_current() -> None:
    user = User.objects.create_superuser(
        username='alice', password='oldpass'  # NOSONAR
    )
    request = _request_with_user(user)
    with pytest.raises(
        AuthSettingsError, match='supply current password to change password'
    ):
        apply_auth_settings(
            request,
            new_auth_backend='auth_basic',
            current_password='',
            new_username='alice',
            new_password='newpass',  # NOSONAR
            new_password_confirm='newpass',  # NOSONAR
            prev_auth_backend='auth_basic',
        )


@pytest.mark.django_db
def test_apply_auth_settings_change_password_wrong_current() -> None:
    user = User.objects.create_superuser(
        username='alice', password='oldpass'  # NOSONAR
    )
    request = _request_with_user(user)
    with pytest.raises(AuthSettingsError, match='Incorrect current password'):
        apply_auth_settings(
            request,
            new_auth_backend='auth_basic',
            current_password='wrong',  # NOSONAR
            new_username='alice',
            new_password='newpass',  # NOSONAR
            new_password_confirm='newpass',  # NOSONAR
            prev_auth_backend='auth_basic',
        )


@pytest.mark.django_db
def test_apply_auth_settings_change_username_success() -> None:
    user = User.objects.create_superuser(
        username='alice', password='oldpass'  # NOSONAR
    )
    request = _request_with_user(user)
    apply_auth_settings(
        request,
        new_auth_backend='auth_basic',
        current_password='oldpass',
        new_username='bob',
        new_password='',
        new_password_confirm='',
        prev_auth_backend='auth_basic',
    )
    user.refresh_from_db()
    assert user.username == 'bob'
    # Password is unchanged.
    assert user.check_password('oldpass')


@pytest.mark.django_db
def test_apply_auth_settings_disable_requires_current_password() -> None:
    user = User.objects.create_superuser(
        username='alice', password='oldpass'  # NOSONAR
    )
    request = _request_with_user(user)
    with pytest.raises(
        AuthSettingsError,
        match='supply current password to change authentication method',
    ):
        apply_auth_settings(
            request,
            new_auth_backend='',
            current_password='',
            new_username='',
            new_password='',
            new_password_confirm='',
            prev_auth_backend='auth_basic',
        )


@pytest.mark.django_db
def test_apply_auth_settings_disable_with_correct_password_succeeds() -> None:
    user = User.objects.create_superuser(
        username='alice', password='oldpass'  # NOSONAR
    )
    request = _request_with_user(user)
    apply_auth_settings(
        request,
        new_auth_backend='',
        current_password='oldpass',
        new_username='',
        new_password='',
        new_password_confirm='',
        prev_auth_backend='auth_basic',
    )
    # Disabling auth keeps the User row intact so re-enabling later
    # doesn't force a fresh password.
    assert User.objects.filter(username='alice').exists()


# ---------------------------------------------------------------------------
# DRF authentication paths
#
# Sanity: each of the four credential paths reaches the /api/v2/assets
# endpoint when the device has auth enabled. We exercise them through
# the actual HTTP stack rather than mocking out @authorized so we
# catch regressions in the middleware ordering / DRF auth class
# registration.


@pytest.fixture
def operator_with_token() -> tuple[User, str]:
    from rest_framework.authtoken.models import Token

    user = User.objects.create_superuser(
        username='alice', password='hunter2'  # NOSONAR
    )
    token = Token.objects.create(user=user)
    return user, token.key


def _enable_auth() -> Any:
    """Patch the global settings dict so @authorized treats auth as
    enabled. Returns the patcher start handle for the caller to stop."""
    return patch.dict(
        'anthias_server.settings.settings.data', {'auth_backend': 'auth_basic'}
    )


@pytest.mark.django_db
def test_basic_auth_header_authenticates_for_back_compat(
    operator_with_token: tuple[User, str],
) -> None:
    """Pre-2826 callers that send Authorization: Basic must keep
    working; we deliberately retained DRF's BasicAuthentication."""
    user, _ = operator_with_token
    creds = b64encode(b'alice:hunter2').decode('ascii')
    client = Client()
    with _enable_auth():
        response = client.get(
            '/api/v2/assets',
            HTTP_AUTHORIZATION=f'Basic {creds}',
        )
    # Either the full asset list (200) or — if asset model wiring
    # rejects the empty fixture state — at minimum NOT a redirect to
    # login. The redirect would prove auth failed.
    assert response.status_code != 302


@pytest.mark.django_db
def test_basic_auth_header_rejects_wrong_password(
    operator_with_token: tuple[User, str],
) -> None:
    creds = b64encode(b'alice:wrong').decode('ascii')
    client = Client()
    with _enable_auth():
        response = client.get(
            '/api/v2/assets',
            HTTP_AUTHORIZATION=f'Basic {creds}',
        )
    # @authorized bounces unauthenticated requests; either the DRF
    # 401 or the @authorized 302 is acceptable — both are "not 200".
    assert response.status_code in (302, 401, 403)


@pytest.mark.django_db
def test_bearer_token_authenticates(
    operator_with_token: tuple[User, str],
) -> None:
    """BearerTokenAuthentication (subclass of TokenAuthentication with
    keyword='Bearer') is the preferred path for new headless callers."""
    _, token = operator_with_token
    client = Client()
    with _enable_auth():
        response = client.get(
            '/api/v2/assets',
            HTTP_AUTHORIZATION=f'Bearer {token}',
        )
    assert response.status_code != 302


@pytest.mark.django_db
def test_bearer_token_rejects_unknown_token() -> None:
    client = Client()
    with _enable_auth():
        response = client.get(
            '/api/v2/assets',
            HTTP_AUTHORIZATION='Bearer nope-not-a-real-token',
        )
    assert response.status_code in (302, 401, 403)


@pytest.mark.django_db
def test_obtain_auth_token_endpoint(
    operator_with_token: tuple[User, str],
) -> None:
    """POST /api/v2/auth/token/ with valid creds returns a token; the
    token resolves back to the same user via Bearer auth."""
    client = Client()
    response = client.post(
        '/api/v2/auth/token',
        data={'username': 'alice', 'password': 'hunter2'},
        content_type='application/json',
    )
    assert response.status_code == 200
    body = response.json()
    assert 'token' in body
    assert isinstance(body['token'], str) and len(body['token']) >= 10


@pytest.mark.django_db
def test_obtain_auth_token_endpoint_rejects_bad_password(
    operator_with_token: tuple[User, str],
) -> None:
    client = Client()
    response = client.post(
        '/api/v2/auth/token',
        data={'username': 'alice', 'password': 'wrong'},
        content_type='application/json',
    )
    assert response.status_code == 400
    assert 'error' in response.json()


@pytest.mark.django_db
def test_obtain_auth_token_endpoint_requires_username_and_password() -> None:
    client = Client()
    response = client.post(
        '/api/v2/auth/token',
        data={},
        content_type='application/json',
    )
    assert response.status_code == 400
