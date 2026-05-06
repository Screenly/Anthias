"""Tests for anthias_server.lib.auth.

The legacy Auth/NoAuth/BasicAuth class hierarchy has been retired —
auth is now Django's built-in (session via DRF
``SessionAuthentication`` + the deprecation-logging
``DeprecatedBasicAuthentication`` for back-compat with pre-2826
headless callers). A UI-managed personal-token path will replace
Basic in a follow-up. What's covered here:

* The hash helpers (round-trip, legacy-format detection) — still used
  by the data migration to gate which conf rows can be promoted into
  User.password.
* The ``@authorized`` shim — feature-flagged, must pass through when
  auth is disabled and redirect to /login otherwise.
* The Session and Basic paths reaching the JSON API.
* ``apply_auth_settings`` — single source of truth for the settings
  page's auth-update flow on both the HTML and DRF code paths.
"""

from __future__ import annotations

from base64 import b64encode
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.http import HttpResponse
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

# Centralised fixture credentials so Sonar's S2068 (potentially-hardcoded
# credential) fires on a single suppressed line per value instead of
# once per assertion across the file. These strings never reach a real
# credential store — they're consumed only by the in-memory test User
# rows below.
_PWD_OLD = 'fixture-old-pwd'  # NOSONAR
_PWD_NEW = 'fixture-new-pwd'  # NOSONAR
_PWD_INITIAL = 'fixture-initial-pwd'  # NOSONAR
_PWD_TOKEN_USER = 'fixture-token-pwd'  # NOSONAR
_PWD_WRONG = 'fixture-wrong-pwd'  # NOSONAR
_PWD_THROWAWAY_1 = 'fixture-throwaway-1'  # NOSONAR
_PWD_THROWAWAY_2 = 'fixture-throwaway-2'  # NOSONAR
_PWD_MISMATCH_A = 'fixture-mismatch-a'  # NOSONAR
_PWD_MISMATCH_B = 'fixture-mismatch-b'  # NOSONAR


# ---------------------------------------------------------------------------
# hash_password / verify_password


@pytest.mark.django_db
def test_hash_password_round_trip() -> None:
    hashed = hash_password(_PWD_INITIAL)
    assert hashed != _PWD_INITIAL
    # Django's hashers always produce an algorithm-prefixed string.
    assert '$' in hashed
    assert verify_password(_PWD_INITIAL, hashed) is True
    assert verify_password(_PWD_WRONG, hashed) is False


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
    assert isinstance(response, HttpResponse)
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
        assert isinstance(response, HttpResponse)
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
    assert isinstance(response, HttpResponse)
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
    """Calls with positional args that don't include a Request object
    raise — the decorator can't know which side to redirect."""
    fake_settings = {'auth_backend': 'auth_basic'}
    monkeypatch.setattr('anthias_server.settings.settings', fake_settings)

    @authorized
    def view(request: Any) -> str:
        return 'ok'

    with pytest.raises(ValueError, match='No request object passed'):
        view('not-a-request')


def test_authorized_finds_request_among_positional_args(
    monkeypatch: Any,
) -> None:
    """A view with extra positional args (e.g. URL captures passed
    positionally, or a DRF method with ``self`` plus a path
    parameter) must still resolve the request — the previous
    ``args[-1]`` heuristic broke for ``def view(self, request,
    asset_id)`` because asset_id ended up where request should be.
    """
    fake_settings = {'auth_backend': 'auth_basic'}
    monkeypatch.setattr('anthias_server.settings.settings', fake_settings)

    @authorized
    def view(self_: Any, request: Any, asset_id: str) -> str:
        return f'ok:{asset_id}'

    factory = RequestFactory()
    req = factory.get('/foo/')
    req.user = MagicMock(is_authenticated=True)
    # Mimic a DRF bound-method call: (self, request, asset_id).
    assert view(object(), req, 'abc-123') == 'ok:abc-123'


# ---------------------------------------------------------------------------
# operator_username — settings-page lookup


@pytest.mark.django_db
def test_operator_username_empty_when_no_user_exists() -> None:
    assert operator_username() == ''


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


def _make_operator(username: str = 'alice', pwd: str = _PWD_OLD) -> User:
    """Centralised superuser factory.

    All scattered ``User.objects.create_superuser(..., password=...)``
    call sites in the tests below come through here so Sonar's
    S6437 (hard-coded password) only sees the kwarg in one place,
    suppressed via NOSONAR. The actual password values used in tests
    are still test-only constants defined at module scope above —
    nothing here is a real credential."""
    return User.objects.create_superuser(
        username=username,
        password=pwd,  # NOSONAR
    )


def _make_user(username: str, pwd: str) -> User:
    """Same idea as ``_make_operator`` but for a regular (non-staff)
    user — used only by ``test_operator_username_returns_first_superuser``
    to verify that ``operator_username()`` skips non-superuser rows."""
    return User.objects.create_user(
        username=username,
        password=pwd,  # NOSONAR
    )


@pytest.mark.django_db
def test_operator_username_returns_first_superuser() -> None:
    _make_user(username='non-admin', pwd=_PWD_THROWAWAY_1)
    _make_operator(username='alice', pwd=_PWD_THROWAWAY_2)
    assert operator_username() == 'alice'


@pytest.mark.django_db
def test_apply_auth_settings_initial_enable_creates_superuser() -> None:
    """Auth disabled → enabling for the first time creates a User with
    is_staff/is_superuser=True so the operator can also reach
    /admin/ via Django's admin."""
    request = _request_with_user(MagicMock(is_authenticated=False))
    apply_auth_settings(
        request,
        new_auth_backend='auth_basic',
        current_pwd='',
        new_username='alice',
        new_pwd=_PWD_INITIAL,
        new_pwd_confirm=_PWD_INITIAL,
        prev_auth_backend='',
    )
    user = User.objects.get(username='alice')
    assert user.is_active and user.is_staff and user.is_superuser
    assert user.check_password(_PWD_INITIAL)


@pytest.mark.django_db
def test_apply_auth_settings_initial_enable_requires_username() -> None:
    request = _request_with_user(MagicMock(is_authenticated=False))
    with pytest.raises(AuthSettingsError, match='Must provide username'):
        apply_auth_settings(
            request,
            new_auth_backend='auth_basic',
            current_pwd='',
            new_username='',
            new_pwd=_PWD_INITIAL,
            new_pwd_confirm=_PWD_INITIAL,
            prev_auth_backend='',
        )


@pytest.mark.django_db
def test_apply_auth_settings_initial_enable_requires_password() -> None:
    request = _request_with_user(MagicMock(is_authenticated=False))
    with pytest.raises(AuthSettingsError, match='Must provide password'):
        apply_auth_settings(
            request,
            new_auth_backend='auth_basic',
            current_pwd='',
            new_username='alice',
            new_pwd='',
            new_pwd_confirm='',
            prev_auth_backend='',
        )


@pytest.mark.django_db
def test_apply_auth_settings_initial_enable_password_mismatch() -> None:
    request = _request_with_user(MagicMock(is_authenticated=False))
    with pytest.raises(AuthSettingsError, match='New passwords do not match'):
        apply_auth_settings(
            request,
            new_auth_backend='auth_basic',
            current_pwd='',
            new_username='alice',
            new_pwd=_PWD_MISMATCH_A,
            new_pwd_confirm=_PWD_MISMATCH_B,
            prev_auth_backend='',
        )


@pytest.mark.django_db
def test_apply_auth_settings_change_password_success() -> None:
    user = _make_operator()
    request = _request_with_user(user)
    apply_auth_settings(
        request,
        new_auth_backend='auth_basic',
        current_pwd=_PWD_OLD,
        new_username='alice',
        new_pwd=_PWD_NEW,
        new_pwd_confirm=_PWD_NEW,
        prev_auth_backend='auth_basic',
    )
    user.refresh_from_db()
    assert user.check_password(_PWD_NEW)


@pytest.mark.django_db
def test_apply_auth_settings_change_password_requires_current() -> None:
    user = _make_operator()
    request = _request_with_user(user)
    with pytest.raises(
        AuthSettingsError, match='supply current password to change password'
    ):
        apply_auth_settings(
            request,
            new_auth_backend='auth_basic',
            current_pwd='',
            new_username='alice',
            new_pwd=_PWD_NEW,
            new_pwd_confirm=_PWD_NEW,
            prev_auth_backend='auth_basic',
        )


@pytest.mark.django_db
def test_apply_auth_settings_change_password_wrong_current() -> None:
    user = _make_operator()
    request = _request_with_user(user)
    with pytest.raises(AuthSettingsError, match='Incorrect current password'):
        apply_auth_settings(
            request,
            new_auth_backend='auth_basic',
            current_pwd=_PWD_WRONG,
            new_username='alice',
            new_pwd=_PWD_NEW,
            new_pwd_confirm=_PWD_NEW,
            prev_auth_backend='auth_basic',
        )


@pytest.mark.django_db
def test_apply_auth_settings_change_username_success() -> None:
    user = _make_operator()
    request = _request_with_user(user)
    apply_auth_settings(
        request,
        new_auth_backend='auth_basic',
        current_pwd=_PWD_OLD,
        new_username='bob',
        new_pwd='',
        new_pwd_confirm='',
        prev_auth_backend='auth_basic',
    )
    user.refresh_from_db()
    assert user.username == 'bob'
    # Password is unchanged.
    assert user.check_password(_PWD_OLD)


@pytest.mark.django_db
def test_apply_auth_settings_disable_requires_current_password() -> None:
    user = _make_operator()
    request = _request_with_user(user)
    with pytest.raises(
        AuthSettingsError,
        match='supply current password to change authentication method',
    ):
        apply_auth_settings(
            request,
            new_auth_backend='',
            current_pwd='',
            new_username='',
            new_pwd='',
            new_pwd_confirm='',
            prev_auth_backend='auth_basic',
        )


@pytest.mark.django_db
def test_apply_auth_settings_disable_with_correct_password_succeeds() -> None:
    user = _make_operator()
    request = _request_with_user(user)
    apply_auth_settings(
        request,
        new_auth_backend='',
        current_pwd=_PWD_OLD,
        new_username='',
        new_pwd='',
        new_pwd_confirm='',
        prev_auth_backend='auth_basic',
    )
    # Disabling auth keeps the User row intact so re-enabling later
    # doesn't force a fresh password.
    assert User.objects.filter(username='alice').exists()


@pytest.mark.django_db
def test_apply_auth_settings_rejects_unknown_backend() -> None:
    """A hand-crafted form POST that smuggles an unknown auth_backend
    value (e.g. 'something-else') must be rejected before any DB or
    conf mutation. Otherwise the caller could persist an unknown
    backend and ``@authorized`` would start enforcing login with no
    operator User row to authenticate against → lockout."""
    request = _request_with_user(MagicMock(is_authenticated=False))
    with pytest.raises(
        AuthSettingsError, match='Unknown authentication backend'
    ):
        apply_auth_settings(
            request,
            new_auth_backend='something-else',
            current_pwd='',
            new_username='alice',
            new_pwd=_PWD_INITIAL,
            new_pwd_confirm=_PWD_INITIAL,
            prev_auth_backend='',
        )
    # No User row was created — the validation fired before any
    # mutation.
    assert not User.objects.filter(username='alice').exists()


@pytest.mark.django_db
def test_apply_auth_settings_initial_enable_rejects_short_password() -> None:
    """``AUTH_PASSWORD_VALIDATORS`` is configured with
    MinimumLengthValidator (default 8). Initial enable must reject a
    too-short password instead of silently storing it."""
    request = _request_with_user(MagicMock(is_authenticated=False))
    with pytest.raises(AuthSettingsError, match='too short'):
        apply_auth_settings(
            request,
            new_auth_backend='auth_basic',
            current_pwd='',
            new_username='alice',
            new_pwd='short',  # NOSONAR - 5 chars; under MinimumLengthValidator's 8
            new_pwd_confirm='short',  # NOSONAR
            prev_auth_backend='',
        )
    # No half-created User row left behind.
    assert not User.objects.filter(username='alice').exists()


@pytest.mark.django_db
def test_apply_auth_settings_change_password_rejects_too_short() -> None:
    """Same validator stack runs on password change."""
    user = _make_operator()
    request = _request_with_user(user)
    with pytest.raises(AuthSettingsError, match='too short'):
        apply_auth_settings(
            request,
            new_auth_backend='auth_basic',
            current_pwd=_PWD_OLD,
            new_username='alice',
            new_pwd='abc',  # NOSONAR - 3 chars
            new_pwd_confirm='abc',  # NOSONAR
            prev_auth_backend='auth_basic',
        )
    # Password unchanged; old still verifies.
    user.refresh_from_db()
    assert user.check_password(_PWD_OLD)


@pytest.mark.django_db
def test_apply_auth_settings_re_enable_with_persisted_user_requires_pwd() -> (
    None
):
    """Privilege-escalation guard: when auth is currently disabled
    (``auth_backend == ''``) but a User row already exists in the DB
    (e.g. preserved by the 0005 migration after enable→disable), an
    UNAUTHENTICATED caller flipping ``auth_backend`` back to
    ``auth_basic`` MUST be challenged for the persisted user's
    current password. Without this gate any LAN attacker could
    re-enable auth with their own credentials and lock the operator
    out."""
    # Pre-existing User row, but no authenticated session — that's
    # the post-disable state.
    _make_operator(username='alice', pwd=_PWD_OLD)
    request = _request_with_user(MagicMock(is_authenticated=False))

    # No current_pwd supplied → must be rejected.
    with pytest.raises(
        AuthSettingsError,
        match='supply current password to change authentication method',
    ):
        apply_auth_settings(
            request,
            new_auth_backend='auth_basic',
            current_pwd='',
            new_username='attacker',
            new_pwd=_PWD_NEW,
            new_pwd_confirm=_PWD_NEW,
            prev_auth_backend='',
        )

    # Wrong current_pwd → also rejected.
    with pytest.raises(AuthSettingsError, match='Incorrect current password'):
        apply_auth_settings(
            request,
            new_auth_backend='auth_basic',
            current_pwd=_PWD_WRONG,
            new_username='attacker',
            new_pwd=_PWD_NEW,
            new_pwd_confirm=_PWD_NEW,
            prev_auth_backend='',
        )

    # Original operator and password are unchanged.
    user = User.objects.get(username='alice')
    assert user.check_password(_PWD_OLD)
    # No attacker User leaked in.
    assert not User.objects.filter(username='attacker').exists()


@pytest.mark.django_db
def test_apply_auth_settings_re_enable_with_correct_pwd_succeeds() -> None:
    """Same setup as the privilege-escalation test, but with the
    correct current password — re-enable should succeed and may
    rotate the operator's username/password as part of the same
    request."""
    _make_operator(username='alice', pwd=_PWD_OLD)
    request = _request_with_user(MagicMock(is_authenticated=False))

    apply_auth_settings(
        request,
        new_auth_backend='auth_basic',
        current_pwd=_PWD_OLD,
        new_username='alice',
        new_pwd=_PWD_NEW,
        new_pwd_confirm=_PWD_NEW,
        prev_auth_backend='',
    )
    user = User.objects.get(username='alice')
    assert user.check_password(_PWD_NEW)


@pytest.mark.django_db
def test_apply_auth_settings_rejects_non_operator_session() -> None:
    """If a recovery superuser was created via
    ``manage.py createsuperuser`` and that recovery account is the
    one currently logged in, ``apply_auth_settings`` must refuse to
    re-key the *operator's* credentials behind their back. The
    canonical operator (first active superuser) is the only account
    that can change auth settings through this flow."""
    # Canonical operator — first active superuser, becomes
    # _persisted_operator().
    operator = _make_operator(username='alice', pwd=_PWD_OLD)
    # Recovery admin from `manage.py createsuperuser`. Also a
    # superuser, but distinct row.
    recovery = _make_operator(username='recovery', pwd=_PWD_NEW)
    request = _request_with_user(recovery)

    with pytest.raises(AuthSettingsError, match='operator account'):
        apply_auth_settings(
            request,
            new_auth_backend='auth_basic',
            current_pwd=_PWD_NEW,
            new_username='hijacked',
            new_pwd=_PWD_THROWAWAY_1,
            new_pwd_confirm=_PWD_THROWAWAY_1,
            prev_auth_backend='auth_basic',
        )
    # Operator's credentials are untouched.
    operator.refresh_from_db()
    assert operator.username == 'alice'
    assert operator.check_password(_PWD_OLD)


@pytest.mark.django_db
def test_apply_auth_settings_noop_does_not_write_user_row() -> None:
    """``apply_auth_settings`` runs on every settings POST (the form
    sends the whole page, including unrelated toggles like
    show_splash). When nothing in the auth section actually changes,
    the operator's User row should NOT be re-saved — that's a wasted
    write to ``auth_user`` on every settings save while auth is
    enabled.

    Detect by snapshotting the password hash before the call and
    asserting it's byte-identical afterwards. Django's PBKDF2 hasher
    re-salts on every ``set_password()``, so a stray save() that ran
    set_password again would change the stored hash even with the
    same plaintext. (We can't stamp ``last_login`` since
    ``apply_auth_settings`` doesn't touch it; the hash check is what
    proves we didn't go through the password update branch.)
    """
    operator = _make_operator()
    request = _request_with_user(operator)
    original_hash = operator.password
    original_pk = operator.pk

    # No new username, no new password, no change of backend.
    apply_auth_settings(
        request,
        new_auth_backend='auth_basic',
        current_pwd='',
        new_username='alice',  # same as existing
        new_pwd='',
        new_pwd_confirm='',
        prev_auth_backend='auth_basic',
    )
    operator.refresh_from_db()
    assert operator.pk == original_pk
    assert operator.password == original_hash


@pytest.mark.django_db
def test_apply_auth_settings_change_username_collision() -> None:
    """Renaming the operator to a username that already exists must
    raise a friendly error instead of leaking IntegrityError."""
    operator = _make_operator(username='alice', pwd=_PWD_OLD)
    # Another user (e.g. one created via `manage.py createsuperuser`)
    _make_user(username='bob', pwd=_PWD_THROWAWAY_1)
    request = _request_with_user(operator)
    with pytest.raises(AuthSettingsError, match='already taken'):
        apply_auth_settings(
            request,
            new_auth_backend='auth_basic',
            current_pwd=_PWD_OLD,
            new_username='bob',  # collides
            new_pwd='',
            new_pwd_confirm='',
            prev_auth_backend='auth_basic',
        )
    # Operator's username was NOT changed.
    operator.refresh_from_db()
    assert operator.username == 'alice'


# ---------------------------------------------------------------------------
# DRF authentication paths
#
# Sanity: each surviving credential path reaches the /api/v2/assets
# endpoint when the device has auth enabled. We exercise them through
# the actual HTTP stack rather than mocking out @authorized so we
# catch regressions in the middleware ordering / DRF auth class
# registration.


@pytest.fixture
def authed_operator() -> User:
    return _make_operator(pwd=_PWD_TOKEN_USER)


def _enable_auth() -> Any:
    """Patch the global settings dict so @authorized treats auth as
    enabled. Returns a ``patch.dict`` context manager — use as
    ``with _enable_auth(): ...`` so the patch is reverted on exit."""
    return patch.dict(
        'anthias_server.settings.settings.data', {'auth_backend': 'auth_basic'}
    )


@pytest.mark.django_db
def test_basic_auth_header_authenticates_for_back_compat(
    authed_operator: User,
) -> None:
    """Pre-2826 callers that send Authorization: Basic must keep
    working; we deliberately retained DRF's BasicAuthentication.

    Pin the explicit success contract (200 + JSON list) rather than
    just ``status_code != 302`` — the looser check would still pass
    if BasicAuthentication regressed to returning 401/403/500, which
    would silently break the back-compat headless path.
    """
    creds = b64encode(f'alice:{_PWD_TOKEN_USER}'.encode()).decode('ascii')
    client = Client()
    with _enable_auth():
        response = client.get(
            '/api/v2/assets',
            HTTP_AUTHORIZATION=f'Basic {creds}',
        )
    assert response.status_code == 200
    # Empty asset list — but the type and shape are what we're locking
    # in: a JSON array, not an HTML login page.
    assert response.headers['Content-Type'].startswith('application/json')
    assert response.json() == []


@pytest.mark.django_db
def test_basic_auth_header_rejects_wrong_password(
    authed_operator: User,
) -> None:
    """Wrong Basic-auth credentials must produce a deterministic
    401 with a WWW-Authenticate challenge — not a 302 (which would
    indicate ``@authorized`` redirected an anonymous request because
    BasicAuthentication wasn't applied at all)."""
    creds = b64encode(f'alice:{_PWD_WRONG}'.encode()).decode('ascii')
    client = Client()
    with _enable_auth():
        response = client.get(
            '/api/v2/assets',
            HTTP_AUTHORIZATION=f'Basic {creds}',
        )
    assert response.status_code == 401
    assert response.headers.get('WWW-Authenticate', '').startswith('Basic')


@pytest.mark.django_db
def test_basic_auth_deprecation_log_throttled(
    authed_operator: User, caplog: pytest.LogCaptureFixture
) -> None:
    """The DEPRECATED Basic-auth log must fire at most once per
    (user, client_ip, path) within the throttle window — a polling
    Anthias-CLI hitting the same endpoint every few seconds would
    otherwise flood the log with identical warnings."""
    import logging

    from anthias_server.lib import auth as auth_module

    # Clear any state left by other tests sharing the in-process
    # throttle dict.
    auth_module._basic_auth_log_seen.clear()

    creds = b64encode(f'alice:{_PWD_TOKEN_USER}'.encode()).decode('ascii')
    client = Client()
    with _enable_auth(), caplog.at_level(logging.WARNING):
        for _ in range(5):
            response = client.get(
                '/api/v2/assets',
                HTTP_AUTHORIZATION=f'Basic {creds}',
            )
            assert response.status_code == 200

    deprecated = [
        r
        for r in caplog.records
        if 'DEPRECATED: HTTP Basic auth' in r.getMessage()
    ]
    # Exactly one warning across all five identical requests.
    assert len(deprecated) == 1, (
        f'expected 1 DEPRECATED log line, got {len(deprecated)}'
    )

    # Different client_ip should not be throttled by the previous
    # entry — a new tuple gets its own log line. Use TEST-NET-1
    # (RFC 5737) so the value is unambiguously a documentation/test
    # placeholder and Sonar's hardcoded-IP hotspot doesn't fire.
    with _enable_auth(), caplog.at_level(logging.WARNING):
        client.get(
            '/api/v2/assets',
            HTTP_AUTHORIZATION=f'Basic {creds}',
            REMOTE_ADDR='192.0.2.42',  # NOSONAR (RFC 5737 doc IP)
        )
    deprecated_after = [
        r
        for r in caplog.records
        if 'DEPRECATED: HTTP Basic auth' in r.getMessage()
    ]
    assert len(deprecated_after) == 2


@pytest.mark.django_db
def test_auth_disabled_ignores_drf_authenticators(
    authed_operator: User,
) -> None:
    """When ``settings['auth_backend'] == ''`` (auth disabled), the
    documented contract is "API is fully open". DRF's stock auth
    classes would violate that — ``SessionAuthentication`` raises
    403 on unsafe methods without ``X-CSRFToken``,
    ``BasicAuthentication`` raises 401 on a malformed header. The
    Anthias-flavoured wrappers (``GatedSessionAuthentication``,
    ``DeprecatedBasicAuthentication``) both inherit
    ``_AuthBackendGated`` which returns ``None`` early when auth is
    disabled, so neither rejection fires.

    This test asserts both shapes pass through to a 200, which is
    impossible with stock DRF classes — the previous wiring would
    have returned 401 on the wrong-Basic-creds case.
    """
    client = Client()
    # auth_backend is '' by default in tests; do NOT enter
    # ``_enable_auth()`` here, that's the whole point.

    # 1. Wrong Basic-auth header. Stock BasicAuthentication would 401.
    creds = b64encode(f'alice:{_PWD_WRONG}'.encode()).decode('ascii')
    response = client.get(
        '/api/v2/assets', HTTP_AUTHORIZATION=f'Basic {creds}'
    )
    assert response.status_code == 200

    # 2. Authenticated session + POST without CSRF token. Stock
    #    SessionAuthentication.enforce_csrf would 403.
    client.force_login(authed_operator)
    response = client.post(
        '/api/v2/assets',
        data='{}',
        content_type='application/json',
    )
    # Either a normal 4xx for body-shape (no name, etc.) or a 200 —
    # but explicitly NOT 403 (the CSRF rejection we're guarding
    # against). The view dispatches and the auth/CSRF gate is silent.
    assert response.status_code != 403
