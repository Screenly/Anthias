from base64 import b64encode
from typing import Any
from unittest.mock import MagicMock

import pytest
from django.test import RequestFactory

from lib import auth
from lib.auth import (
    Auth,
    BasicAuth,
    NoAuth,
    _is_legacy_sha256,
    authorized,
    hash_password,
    verify_password,
)


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


def test_no_auth_is_authenticated_always_true() -> None:
    backend = NoAuth()
    factory = RequestFactory()
    assert backend.is_authenticated(factory.get('/')) is True


def test_no_auth_authenticate_returns_none() -> None:
    backend = NoAuth()
    # NoAuth.authenticate returns None unconditionally (annotated -> None).
    backend.authenticate()


def test_no_auth_check_password_always_true() -> None:
    backend = NoAuth()
    assert backend.check_password('anything') is True


@pytest.fixture
def basic_auth_settings() -> dict[str, Any]:
    return {'user': 'alice', 'password': ''}


@pytest.fixture
def basic_auth(basic_auth_settings: dict[str, Any]) -> BasicAuth:
    return BasicAuth(basic_auth_settings)


@pytest.mark.django_db
def test_basic_auth_check_password_round_trip(
    basic_auth: BasicAuth, basic_auth_settings: dict[str, Any]
) -> None:
    basic_auth_settings['password'] = hash_password('s3cret')
    assert basic_auth.check_password('s3cret') is True
    assert basic_auth.check_password('nope') is False


@pytest.mark.django_db
def test_basic_auth_internal_check(
    basic_auth: BasicAuth, basic_auth_settings: dict[str, Any]
) -> None:
    basic_auth_settings['password'] = hash_password('s3cret')
    assert basic_auth._check('alice', 's3cret') is True
    assert basic_auth._check('alice', 'wrong') is False
    assert basic_auth._check('bob', 's3cret') is False


@pytest.mark.django_db
def test_basic_auth_authorization_header(
    basic_auth: BasicAuth, basic_auth_settings: dict[str, Any]
) -> None:
    basic_auth_settings['password'] = hash_password('s3cret')
    factory = RequestFactory()

    # Correct credentials.
    creds = b64encode(b'alice:s3cret').decode('ascii')
    request = factory.get('/', HTTP_AUTHORIZATION=f'Basic {creds}')
    request.session = {}  # type: ignore[assignment]
    assert basic_auth.is_authenticated(request) is True

    # Wrong password.
    creds_bad = b64encode(b'alice:wrong').decode('ascii')
    request = factory.get('/', HTTP_AUTHORIZATION=f'Basic {creds_bad}')
    request.session = {}  # type: ignore[assignment]
    assert basic_auth.is_authenticated(request) is False


@pytest.mark.django_db
def test_basic_auth_password_with_colon(
    basic_auth: BasicAuth, basic_auth_settings: dict[str, Any]
) -> None:
    """RFC 7617 allows ':' inside the password portion."""
    basic_auth_settings['password'] = hash_password('pa:ss:word')
    factory = RequestFactory()
    creds = b64encode(b'alice:pa:ss:word').decode('ascii')
    request = factory.get('/', HTTP_AUTHORIZATION=f'Basic {creds}')
    request.session = {}  # type: ignore[assignment]
    assert basic_auth.is_authenticated(request) is True


def test_basic_auth_malformed_authorization_header_returns_false(
    basic_auth: BasicAuth,
) -> None:
    factory = RequestFactory()
    # Not valid base64.
    request = factory.get('/', HTTP_AUTHORIZATION='Basic !!!notbase64!!!')
    request.session = {}  # type: ignore[assignment]
    assert basic_auth.is_authenticated(request) is False


def test_basic_auth_authorization_header_no_colon(
    basic_auth: BasicAuth,
) -> None:
    factory = RequestFactory()
    # base64 of 'alice' (no colon at all → unauthenticated)
    creds = b64encode(b'alice').decode('ascii')
    request = factory.get('/', HTTP_AUTHORIZATION=f'Basic {creds}')
    request.session = {}  # type: ignore[assignment]
    assert basic_auth.is_authenticated(request) is False


def test_basic_auth_unsupported_scheme(basic_auth: BasicAuth) -> None:
    factory = RequestFactory()
    request = factory.get('/', HTTP_AUTHORIZATION='Bearer abcdef')
    request.session = {}  # type: ignore[assignment]
    assert basic_auth.is_authenticated(request) is False


def test_basic_auth_authorization_header_short(basic_auth: BasicAuth) -> None:
    factory = RequestFactory()
    request = factory.get('/', HTTP_AUTHORIZATION='Basic')
    request.session = {}  # type: ignore[assignment]
    # Single token doesn't split into [type, data] → falls through.
    assert basic_auth.is_authenticated(request) is False


@pytest.mark.django_db
def test_basic_auth_session_login(
    basic_auth: BasicAuth, basic_auth_settings: dict[str, Any]
) -> None:
    basic_auth_settings['password'] = hash_password('s3cret')
    factory = RequestFactory()
    request = factory.get('/')
    request.session = {  # type: ignore[assignment]
        'auth_username': 'alice',
        'auth_password': 's3cret',
    }
    assert basic_auth.is_authenticated(request) is True


def test_basic_auth_no_credentials(basic_auth: BasicAuth) -> None:
    factory = RequestFactory()
    request = factory.get('/')
    request.session = {}  # type: ignore[assignment]
    assert basic_auth.is_authenticated(request) is False


def test_basic_auth_template(
    basic_auth: BasicAuth, basic_auth_settings: dict[str, Any]
) -> None:
    template, ctx = basic_auth.template
    assert template == 'auth_basic.html'
    assert ctx == {'user': basic_auth_settings['user']}


@pytest.mark.django_db
def test_basic_auth_authenticate_redirects_to_login(
    basic_auth: BasicAuth,
) -> None:
    response = basic_auth.authenticate()
    # `redirect()` returns an HttpResponseRedirect (status 302).
    assert response.status_code == 302
    assert '/login' in response['Location']


@pytest.mark.django_db
def test_basic_auth_update_settings_initial_set(
    basic_auth: BasicAuth, basic_auth_settings: dict[str, Any]
) -> None:
    factory = RequestFactory()
    request = factory.post(
        '/', {'user': 'alice', 'password': 'pw1', 'password2': 'pw1'}
    )
    basic_auth.update_settings(request, current_pass_correct=None)
    assert basic_auth_settings['user'] == 'alice'
    assert basic_auth_settings['password']  # hashed, non-empty
    assert basic_auth_settings['password'] != 'pw1'


@pytest.mark.django_db
def test_basic_auth_update_settings_initial_no_password_raises(
    basic_auth: BasicAuth, basic_auth_settings: dict[str, Any]
) -> None:
    factory = RequestFactory()
    request = factory.post('/', {'user': 'alice', 'password': ''})
    with pytest.raises(ValueError, match='Must provide password'):
        basic_auth.update_settings(request, current_pass_correct=None)


@pytest.mark.django_db
def test_basic_auth_update_settings_initial_no_username_raises(
    basic_auth: BasicAuth, basic_auth_settings: dict[str, Any]
) -> None:
    factory = RequestFactory()
    request = factory.post('/', {'user': '', 'password': 'pw'})
    with pytest.raises(ValueError, match='Must provide username'):
        basic_auth.update_settings(request, current_pass_correct=None)


@pytest.mark.django_db
def test_basic_auth_update_settings_initial_password_mismatch(
    basic_auth: BasicAuth, basic_auth_settings: dict[str, Any]
) -> None:
    factory = RequestFactory()
    request = factory.post(
        '/', {'user': 'alice', 'password': 'a', 'password2': 'b'}
    )
    with pytest.raises(ValueError, match='New passwords do not match'):
        basic_auth.update_settings(request, current_pass_correct=None)


@pytest.mark.django_db
def test_basic_auth_update_settings_change_user_requires_current_password(
    basic_auth: BasicAuth, basic_auth_settings: dict[str, Any]
) -> None:
    basic_auth_settings['password'] = hash_password('old')
    factory = RequestFactory()
    request = factory.post('/', {'user': 'bob', 'password': ''})

    with pytest.raises(
        ValueError, match='supply current password to change username'
    ):
        basic_auth.update_settings(request, current_pass_correct=None)

    with pytest.raises(ValueError, match='Incorrect current password'):
        basic_auth.update_settings(request, current_pass_correct=False)


@pytest.mark.django_db
def test_basic_auth_update_settings_change_password_requires_current(
    basic_auth: BasicAuth, basic_auth_settings: dict[str, Any]
) -> None:
    basic_auth_settings['password'] = hash_password('old')
    factory = RequestFactory()
    request = factory.post(
        '/',
        {'user': 'alice', 'password': 'newpw', 'password2': 'newpw'},
    )

    with pytest.raises(
        ValueError, match='supply current password to change password'
    ):
        basic_auth.update_settings(request, current_pass_correct=None)

    with pytest.raises(ValueError, match='Incorrect current password'):
        basic_auth.update_settings(request, current_pass_correct=False)


@pytest.mark.django_db
def test_basic_auth_update_settings_change_password_mismatch(
    basic_auth: BasicAuth, basic_auth_settings: dict[str, Any]
) -> None:
    basic_auth_settings['password'] = hash_password('old')
    factory = RequestFactory()
    request = factory.post(
        '/',
        {'user': 'alice', 'password': 'a', 'password2': 'b'},
    )
    with pytest.raises(ValueError, match='New passwords do not match'):
        basic_auth.update_settings(request, current_pass_correct=True)


@pytest.mark.django_db
def test_basic_auth_update_settings_change_password_success(
    basic_auth: BasicAuth, basic_auth_settings: dict[str, Any]
) -> None:
    basic_auth_settings['password'] = hash_password('old')
    factory = RequestFactory()
    request = factory.post(
        '/',
        {'user': 'alice', 'password': 'newpw', 'password2': 'newpw'},
    )
    basic_auth.update_settings(request, current_pass_correct=True)
    assert verify_password('newpw', basic_auth_settings['password'])


@pytest.mark.django_db
def test_basic_auth_update_settings_change_user_success(
    basic_auth: BasicAuth, basic_auth_settings: dict[str, Any]
) -> None:
    basic_auth_settings['password'] = hash_password('old')
    factory = RequestFactory()
    request = factory.post('/', {'user': 'bob', 'password': ''})
    basic_auth.update_settings(request, current_pass_correct=True)
    assert basic_auth_settings['user'] == 'bob'
    # Password not modified.
    assert verify_password('old', basic_auth_settings['password'])


def test_auth_base_class_defaults() -> None:
    """Cover the base Auth methods that subclasses don't always override."""

    class _Concrete(Auth):
        def authenticate(self) -> None:
            return None

    backend = _Concrete()
    factory = RequestFactory()
    # Default is_authenticated returns False.
    assert backend.is_authenticated(factory.get('/')) is False
    # Default check_password returns False.
    assert backend.check_password('anything') is False
    # Default template returns None.
    assert backend.template is None
    # Default update_settings is a no-op.
    backend.update_settings(factory.post('/'), current_pass_correct=None)


def test_authenticate_if_needed_returns_none_when_authenticated() -> None:
    """When the user is already authenticated, we don't re-authenticate."""
    backend = NoAuth()
    factory = RequestFactory()
    assert backend.authenticate_if_needed(factory.get('/')) is None


@pytest.mark.django_db
def test_authenticate_if_needed_initiates_when_not_authenticated() -> None:
    settings = {'user': 'a', 'password': ''}
    backend = BasicAuth(settings)
    factory = RequestFactory()
    request = factory.get('/')
    request.session = {}  # type: ignore[assignment]
    response = backend.authenticate_if_needed(request)
    assert response is not None
    assert response.status_code == 302


def test_authenticate_if_needed_handles_value_error() -> None:
    """503 when the auth backend cannot answer (raises ValueError)."""

    class _Broken(Auth):
        def is_authenticated(self, request: Any) -> bool:
            raise ValueError('something wrong')

        def authenticate(self) -> None:
            return None

    factory = RequestFactory()
    response = _Broken().authenticate_if_needed(factory.get('/'))
    assert response is not None
    assert response.status_code == 503
    assert b'something wrong' in response.content


def test_authorized_passthrough_when_no_auth(monkeypatch: Any) -> None:
    """If settings.auth is falsy, the wrapped view is called directly."""
    fake_settings = MagicMock()
    fake_settings.auth = None
    monkeypatch.setattr('settings.settings', fake_settings)

    @authorized
    def view(request: Any) -> str:
        return 'ok'

    factory = RequestFactory()
    assert view(factory.get('/')) == 'ok'


def test_authorized_returns_auth_response_when_required(
    monkeypatch: Any,
) -> None:
    """If settings.auth requires authentication, that response is returned."""
    sentinel_response = MagicMock(name='auth-response')
    auth_backend = MagicMock()
    auth_backend.authenticate_if_needed.return_value = sentinel_response

    fake_settings = MagicMock()
    fake_settings.auth = auth_backend
    monkeypatch.setattr('settings.settings', fake_settings)

    @authorized
    def view(request: Any) -> str:
        return 'ok'

    factory = RequestFactory()
    assert view(factory.get('/')) is sentinel_response


def test_authorized_calls_view_when_authenticated(monkeypatch: Any) -> None:
    auth_backend = MagicMock()
    auth_backend.authenticate_if_needed.return_value = None
    fake_settings = MagicMock()
    fake_settings.auth = auth_backend
    monkeypatch.setattr('settings.settings', fake_settings)

    @authorized
    def view(request: Any) -> str:
        return 'ok'

    factory = RequestFactory()
    assert view(factory.get('/')) == 'ok'


def test_authorized_no_args_raises(monkeypatch: Any) -> None:
    fake_settings = MagicMock()
    fake_settings.auth = MagicMock()
    monkeypatch.setattr('settings.settings', fake_settings)

    @authorized
    def view() -> str:
        return 'ok'

    with pytest.raises(ValueError, match='No request object passed'):
        view()


def test_authorized_non_request_arg_raises(monkeypatch: Any) -> None:
    fake_settings = MagicMock()
    fake_settings.auth = MagicMock()
    monkeypatch.setattr('settings.settings', fake_settings)

    @authorized
    def view(request: Any) -> str:
        return 'ok'

    with pytest.raises(ValueError, match='not of type HttpRequest'):
        view('not-a-request')


def test_module_level_linux_user_constant() -> None:
    # Sanity: the constant is read at import and exposed for callers.
    assert isinstance(auth.LINUX_USER, str)
    assert auth.LINUX_USER  # non-empty
