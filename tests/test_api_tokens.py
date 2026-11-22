"""API bearer tokens: storage, the DRF authenticator, and the settings UI.

The contract under test:

* only a SHA-256 digest and a short display prefix are stored;
* ``Authorization: Bearer <token>`` authenticates against the v2 (and
  older) API while authentication is enabled, and a bad, revoked or
  orphaned token gets a 401;
* while authentication is disabled tokens can't be created and the
  header is ignored (the API is open by contract), so a token never
  looks like it is protecting anything;
* the settings page creates (plaintext shown once), lists and revokes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.db.models import QuerySet
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from anthias_server.app.models import ApiToken
from anthias_server.lib import api_tokens

_PWD = 'fixture-token-operator-pwd'  # NOSONAR


@pytest.fixture
def operator() -> User:
    return User.objects.create_superuser(
        username='operator',
        password=_PWD,  # NOSONAR
    )


@pytest.fixture
def auth_on(_isolated_settings_conf: None) -> Iterator[None]:
    """Turn auth on in the (per-test) conf file, not only in memory:
    the settings page calls ``settings.load()`` and would otherwise
    read it back as off."""
    from anthias_server.settings import settings

    settings['auth_backend'] = 'auth_basic'
    settings.save()
    yield


def _bearer(token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {token}'}


# ---------------------------------------------------------------------------
# lib.api_tokens


@pytest.mark.django_db
def test_create_token_stores_only_hash_and_prefix(
    operator: User, auth_on: None
) -> None:
    token, plaintext = api_tokens.create_token(operator, '  Controller  ')

    assert plaintext.startswith(api_tokens.TOKEN_PREFIX)
    assert len(plaintext) > 40
    token.refresh_from_db()
    assert token.name == 'Controller'
    assert token.user == operator
    assert token.token_hash == hashlib.sha256(plaintext.encode()).hexdigest()
    assert token.prefix == plaintext[: api_tokens.DISPLAY_PREFIX_LEN]
    assert plaintext not in {token.prefix, token.token_hash, token.name}
    assert token.last_used_at is None
    assert not token.is_revoked


@pytest.mark.django_db
def test_create_token_generates_unique_tokens(
    operator: User, auth_on: None
) -> None:
    _, first = api_tokens.create_token(operator, 'a')
    _, second = api_tokens.create_token(operator, 'b')
    assert first != second


@pytest.mark.django_db
def test_create_token_refused_while_auth_disabled(operator: User) -> None:
    with pytest.raises(api_tokens.ApiTokenError, match='authentication'):
        api_tokens.create_token(operator, 'Controller')
    assert not ApiToken.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize('name', ['', '   ', 'x' * 101])
def test_create_token_validates_name(
    operator: User, auth_on: None, name: str
) -> None:
    with pytest.raises(api_tokens.ApiTokenError):
        api_tokens.create_token(operator, name)
    assert not ApiToken.objects.exists()


@pytest.mark.django_db
def test_create_token_requires_signed_in_user(auth_on: None) -> None:
    with pytest.raises(api_tokens.ApiTokenError, match='Sign in'):
        api_tokens.create_token(AnonymousUser(), 'Controller')


@pytest.mark.django_db
def test_authenticate_token_matches_and_touches_last_used(
    operator: User, auth_on: None
) -> None:
    token, plaintext = api_tokens.create_token(operator, 'c')

    found = api_tokens.authenticate_token(plaintext)

    assert found is not None
    assert found.pk == token.pk
    token.refresh_from_db()
    assert token.last_used_at is not None


@pytest.mark.django_db
def test_authenticate_token_throttles_last_used_writes(
    operator: User, auth_on: None
) -> None:
    token, plaintext = api_tokens.create_token(operator, 'c')
    recent = timezone.now() - timedelta(seconds=5)
    ApiToken.objects.filter(pk=token.pk).update(last_used_at=recent)

    api_tokens.authenticate_token(plaintext)
    token.refresh_from_db()
    assert token.last_used_at == recent

    stale = timezone.now() - api_tokens.LAST_USED_RESOLUTION * 2
    ApiToken.objects.filter(pk=token.pk).update(last_used_at=stale)
    api_tokens.authenticate_token(plaintext)
    token.refresh_from_db()
    assert token.last_used_at is not None
    assert token.last_used_at > stale


@pytest.mark.django_db
@pytest.mark.parametrize('previously_used', [False, True])
def test_last_used_throttle_handles_concurrent_requests(
    operator: User, auth_on: None, previously_used: bool
) -> None:
    """A request holding a stale row must not repeat another request's write."""
    token, plaintext = api_tokens.create_token(operator, 'Controller')
    if previously_used:
        ApiToken.objects.filter(pk=token.pk).update(
            last_used_at=timezone.now() - timedelta(minutes=2)
        )
    recent = timezone.now() - timedelta(seconds=5)
    first = QuerySet.first

    def concurrent_first(queryset: QuerySet[ApiToken]) -> ApiToken | None:
        snapshot = first(queryset)
        # Another request updates the row after this one's SELECT.
        ApiToken.objects.filter(pk=token.pk).update(last_used_at=recent)
        return snapshot

    with patch.object(QuerySet, 'first', concurrent_first):
        assert api_tokens.authenticate_token(plaintext) is not None

    token.refresh_from_db()
    assert token.last_used_at == recent


@pytest.mark.django_db
def test_authenticate_token_rejects_unknown_revoked_and_inactive(
    operator: User, auth_on: None
) -> None:
    token, plaintext = api_tokens.create_token(operator, 'c')

    assert api_tokens.authenticate_token('') is None
    assert api_tokens.authenticate_token(plaintext + 'x') is None

    operator.is_active = False
    operator.save(update_fields=['is_active'])
    assert api_tokens.authenticate_token(plaintext) is None
    operator.is_active = True
    operator.save(update_fields=['is_active'])

    assert api_tokens.revoke_token(token.pk) is True
    assert api_tokens.authenticate_token(plaintext) is None
    assert api_tokens.revoke_token(token.pk) is False


@pytest.mark.django_db
def test_authenticate_token_ignored_while_auth_disabled(
    operator: User,
) -> None:
    with patch.dict(
        'anthias_server.settings.settings.data',
        {'auth_backend': 'auth_basic'},
    ):
        token, plaintext = api_tokens.create_token(operator, 'c')

    assert api_tokens.authenticate_token(plaintext) is None
    token.refresh_from_db()
    assert token.last_used_at is None


# ---------------------------------------------------------------------------
# BearerTokenAuthentication against the API


@pytest.mark.django_db
@pytest.mark.parametrize(
    'path',
    [
        '/api/v2/info',
        '/api/v2/assets',
        '/api/v1.2/assets',
        '/api/v1.1/assets',
        '/api/v1/assets',
    ],
)
def test_bearer_token_authenticates_api(
    operator: User, auth_on: None, path: str
) -> None:
    _, plaintext = api_tokens.create_token(operator, 'c')

    response = Client().get(path, headers=_bearer(plaintext))

    assert response.status_code == 200, response.content
    assert response.headers['Content-Type'].startswith('application/json')


@pytest.mark.django_db
def test_bearer_token_can_write(operator: User, auth_on: None) -> None:
    """Writes need no CSRF token: CSRF only guards cookie sessions."""
    _, plaintext = api_tokens.create_token(operator, 'c')

    response = Client(enforce_csrf_checks=True).post(
        '/api/v2/assets',
        data={
            'name': 'Example',
            'uri': 'https://example.com',
            'mimetype': 'webpage',
            'is_enabled': True,
            'start_date': '2026-01-01T00:00:00Z',
            'end_date': '2099-01-01T00:00:00Z',
            'duration': 10,
            'play_order': 0,
            'nocache': False,
            'skip_asset_check': True,
        },
        content_type='application/json',
        headers=_bearer(plaintext),
    )

    assert response.status_code == 201, response.content


@pytest.mark.django_db
@pytest.mark.parametrize(
    'header',
    [
        'Bearer anthias_not-a-real-token',
        'Bearer',
        'Bearer two parts',
    ],
)
def test_bad_bearer_header_is_401(
    operator: User, auth_on: None, header: str
) -> None:
    response = Client().get('/api/v2/assets', HTTP_AUTHORIZATION=header)
    assert response.status_code == 401


@pytest.mark.django_db
def test_revoked_token_is_401(operator: User, auth_on: None) -> None:
    token, plaintext = api_tokens.create_token(operator, 'c')
    client = Client()
    assert client.get(
        '/api/v2/assets', headers=_bearer(plaintext)
    ).status_code == (200)

    api_tokens.revoke_token(token.pk)

    response = client.get('/api/v2/assets', headers=_bearer(plaintext))
    assert response.status_code == 401
    assert 'revoked' in response.json()['detail']


@pytest.mark.django_db
def test_no_credentials_still_redirects_to_login(auth_on: None) -> None:
    """Adding the bearer class must not change the anonymous path."""
    response = Client().get('/api/v2/assets')
    assert response.status_code == 302
    assert '/login/' in response['Location']


@pytest.mark.django_db
def test_bearer_header_ignored_while_auth_disabled(operator: User) -> None:
    """Auth off = API open. A token (valid or not) neither helps nor
    hurts, and it is not recorded as used."""
    with patch.dict(
        'anthias_server.settings.settings.data',
        {'auth_backend': 'auth_basic'},
    ):
        token, plaintext = api_tokens.create_token(operator, 'c')

    client = Client()
    assert client.get(
        '/api/v2/assets', headers=_bearer(plaintext)
    ).status_code == (200)
    assert (
        client.get('/api/v2/assets', headers=_bearer('garbage')).status_code
        == 200
    )
    token.refresh_from_db()
    assert token.last_used_at is None


@pytest.mark.django_db
def test_bearer_scheme_in_openapi_schema() -> None:
    response = Client().get('/api/schema/')
    assert response.status_code == 200
    assert b'bearerAuth' in response.content


# ---------------------------------------------------------------------------
# Settings page


@pytest.fixture
def operator_client(operator: User) -> Client:
    client = Client()
    client.force_login(operator)
    return client


@pytest.mark.django_db
def test_settings_page_lists_tokens(
    operator: User, operator_client: Client, auth_on: None
) -> None:
    api_tokens.create_token(operator, 'Lobby controller')
    revoked, _ = api_tokens.create_token(operator, 'Old laptop')
    api_tokens.revoke_token(revoked.pk)

    body = operator_client.get(reverse('anthias_app:settings')).content
    body_text = body.decode()

    assert 'API tokens' in body_text
    assert 'Lobby controller' in body_text
    assert 'Old laptop' not in body_text
    assert 'id="btn-create-api-token"' in body_text


@pytest.mark.django_db
def test_settings_page_explains_tokens_need_auth() -> None:
    body = Client().get(reverse('anthias_app:settings')).content.decode()
    assert 'API tokens' in body
    assert 'Authentication is disabled' in body
    assert 'id="btn-create-api-token"' not in body


@pytest.mark.django_db
def test_create_via_htmx_shows_plaintext_once(
    operator_client: Client, auth_on: None
) -> None:
    response = operator_client.post(
        reverse('anthias_app:settings_api_tokens_create'),
        data={'name': 'Controller'},
        HTTP_HX_REQUEST='true',
    )

    assert response.status_code == 200
    assert response['Cache-Control'] == 'no-store'
    token = ApiToken.objects.get()
    body = response.content.decode()
    assert 'id="api-tokens"' in body
    assert '<html' not in body
    assert 'won&#x27;t be shown again' in body or "won't be shown" in body
    start = body.index('value="', body.index('id="new-api-token"'))
    plaintext = body[start + len('value="') :].split('"', 1)[0]
    assert api_tokens.hash_token(plaintext) == token.token_hash

    listing = operator_client.get(reverse('anthias_app:settings'))
    assert plaintext not in listing.content.decode()
    assert token.prefix in listing.content.decode()


@pytest.mark.django_db
def test_create_without_htmx_renders_full_page(
    operator_client: Client, auth_on: None
) -> None:
    response = operator_client.post(
        reverse('anthias_app:settings_api_tokens_create'),
        data={'name': 'Controller'},
    )
    assert response.status_code == 200
    assert 'id="new-api-token"' in response.content.decode()
    assert '<html' in response.content.decode()


@pytest.mark.django_db
def test_create_refused_while_auth_disabled() -> None:
    response = Client().post(
        reverse('anthias_app:settings_api_tokens_create'),
        data={'name': 'Controller'},
        HTTP_HX_REQUEST='true',
    )
    assert response.status_code == 200
    assert 'Turn on authentication' in response.content.decode()
    assert not ApiToken.objects.exists()


@pytest.mark.django_db
def test_create_requires_login_when_auth_on(auth_on: None) -> None:
    response = Client().post(
        reverse('anthias_app:settings_api_tokens_create'),
        data={'name': 'Controller'},
    )
    assert response.status_code == 302
    assert not ApiToken.objects.exists()


@pytest.mark.django_db
def test_revoke_via_settings(
    operator: User, operator_client: Client, auth_on: None
) -> None:
    token, plaintext = api_tokens.create_token(operator, 'Controller')

    response = operator_client.post(
        reverse('anthias_app:settings_api_tokens_revoke', args=[token.pk]),
        HTTP_HX_REQUEST='true',
    )

    assert response.status_code == 200
    token.refresh_from_db()
    assert token.is_revoked
    assert 'Controller' not in response.content.decode()
    assert (
        Client().get('/api/v2/assets', headers=_bearer(plaintext)).status_code
        == 401
    )

    again = operator_client.post(
        reverse('anthias_app:settings_api_tokens_revoke', args=[token.pk]),
        HTTP_HX_REQUEST='true',
    )
    assert 'already revoked' in again.content.decode()


@pytest.mark.django_db
def test_revoke_allowed_while_auth_disabled(operator: User) -> None:
    with patch.dict(
        'anthias_server.settings.settings.data',
        {'auth_backend': 'auth_basic'},
    ):
        token, _ = api_tokens.create_token(operator, 'Controller')

    Client().post(
        reverse('anthias_app:settings_api_tokens_revoke', args=[token.pk]),
        HTTP_HX_REQUEST='true',
    )

    token.refresh_from_db()
    assert token.is_revoked


@pytest.mark.django_db
def test_token_cannot_reach_settings_ui(operator: User, auth_on: None) -> None:
    """Tokens are an API credential only: one can't be used to mint or
    revoke tokens through the HTML settings views."""
    _, plaintext = api_tokens.create_token(operator, 'c')

    response = Client().post(
        reverse('anthias_app:settings_api_tokens_create'),
        data={'name': 'escalation'},
        headers=_bearer(plaintext),
    )

    assert response.status_code == 302
    assert ApiToken.objects.count() == 1
