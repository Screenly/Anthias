"""Multi-screen management, screen side.

The contract under test (see ``anthias_server.lib.management``):

* the :8443 m2m site exists only while this screen is managed (or is a
  controller) and auth is on, serves only ``/api/v2/*`` with the node
  certificate, and tags requests ``X-Anthias-Listener: m2m``;
* joining parses Proxmox-style join information, refuses without auth
  or while already managed, connects pinned to the snippet's
  fingerprint, and on any failure leaves no active token and the site
  as it was;
* the screen stores only the controller token's hash, and revoking it
  ends management;
* leaving (locally, or by the controller over DELETE /api/v2/pairing)
  revokes the token and withdraws the site.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Self
from unittest.mock import MagicMock

import pytest
import requests
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from anthias_server.app.models import ApiToken, ManagementController
from anthias_server.lib import api_tokens, management

_PWD = 'fixture-management-operator-pwd'  # NOSONAR
CONTROLLER_FP = 'cd' * 32


@pytest.fixture
def operator() -> User:
    return User.objects.create_superuser(username='operator', password=_PWD)


@pytest.fixture
def auth_on(_isolated_settings_conf: None) -> Iterator[None]:
    from anthias_server.settings import settings

    settings['auth_backend'] = 'auth_basic'
    settings['management_enabled'] = False
    settings['controller_mode'] = False
    settings.save()
    yield


@pytest.fixture(autouse=True)
def stub_node_and_caddy(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Real node identity (isolated by conftest), Caddy apply mocked."""
    apply = MagicMock(name='caddy.apply')
    monkeypatch.setattr('anthias_server.lib.caddy.apply', apply)
    return apply


@pytest.fixture
def node_fp() -> str:
    from anthias_server.lib import node_identity

    return node_identity.ensure_node_identity().fingerprint


def _snippet(**overrides: Any) -> str:
    data = {
        'v': 1,
        'url': 'https://controller.lan:8443',
        'fp': CONTROLLER_FP,
        'secret': 'c2VjcmV0LXNlY3JldA',
        'name': 'Lobby controller',
    }
    data.update(overrides)
    raw = json.dumps(data).encode()
    return 'anthias-join:' + base64.urlsafe_b64encode(raw).decode().rstrip('=')


class _FakeResponse:
    def __init__(self, status: int, body: Any = None) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        if self._body is None:
            raise ValueError('no body')
        return self._body


class _FakeSession:
    def __init__(self, outcome: Any, calls: list[dict[str, Any]]) -> None:
        self._outcome = outcome
        self._calls = calls

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self._calls.append({'url': url, **kwargs})
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        if callable(self._outcome):
            result: _FakeResponse = self._outcome()
            return result
        response: _FakeResponse = self._outcome
        return response


@pytest.fixture
def controller_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """Configure what the (pinned) controller answers to the join."""
    state: dict[str, Any] = {
        'outcome': _FakeResponse(
            202,
            {
                'status': 'pending',
                'request_id': 'rq-1',
                'check_code': 'ABCD-1234',
                'controller_name': 'Lobby',
            },
        ),
        'calls': [],
        'pins': [],
    }

    def fake_pinned_session(
        fingerprint: str, client_cert: Any = None
    ) -> _FakeSession:
        state['pins'].append(fingerprint)
        return _FakeSession(state['outcome'], state['calls'])

    monkeypatch.setattr(
        'anthias_server.lib.pinned_http.pinned_session', fake_pinned_session
    )
    return state


def _approve(controller: ManagementController) -> tuple[Any, Any]:
    """What the controller does when the operator approves: a real
    certificate from a (test) CA, for this screen's node key."""
    from anthias_server.lib import node_identity, pki

    ca_key = pki.new_key()
    ca_cert = pki.make_ca(ca_key, 'Test controller CA')
    identity = node_identity.ensure_node_identity()
    node_key = pki.load_key(identity.key_path.read_bytes())
    cert = pki.issue_client_cert(
        ca_cert,
        ca_key,
        node_key.public_key(),
        identity.fingerprint,
        pki.ROLE_SCREEN,
    )
    crl = pki.make_crl(ca_cert, ca_key, [], 1)
    management.install_approval(
        controller.token_id,
        pki.cert_pem(cert),
        pki.cert_pem(ca_cert),
        pki.crl_pem(crl),
        'Lobby',
    )
    return ca_cert, ca_key


def _join_and_approve(**overrides: Any) -> ManagementController:
    controller = management.join(_snippet(**overrides))
    _approve(controller)
    controller.refresh_from_db()
    return controller


# ---------------------------------------------------------------------------
# join information


def test_parse_join_info_roundtrip() -> None:
    info = management.parse_join_info('  ' + _snippet() + '\n')
    assert info.url == 'https://controller.lan:8443'
    assert info.fingerprint == CONTROLLER_FP
    assert info.secret == 'c2VjcmV0LXNlY3JldA'
    assert info.name == 'Lobby controller'


def test_parse_join_info_accepts_colon_fingerprint_and_padding() -> None:
    colon_fp = ':'.join(['CD'] * 32)
    data = json.dumps(
        {
            'v': 1,
            'url': 'https://10.0.0.5:8443/',
            'fp': colon_fp,
            'secret': 's',
        }
    ).encode()
    padded = 'anthias-join:' + base64.urlsafe_b64encode(data).decode()
    info = management.parse_join_info(padded)
    assert info.fingerprint == CONTROLLER_FP
    assert info.url == 'https://10.0.0.5:8443'
    assert info.name == '10.0.0.5'


@pytest.mark.parametrize(
    'snippet',
    [
        '',
        'hello',
        'anthias-join:!!!not-base64!!!',
        'anthias-join:' + base64.urlsafe_b64encode(b'[1]').decode(),
        _snippet(v=2),
        _snippet(url='http://controller.lan:8443'),
        _snippet(url='https://'),
        _snippet(fp='abc'),
        _snippet(secret=''),
    ],
)
def test_parse_join_info_rejects_bad_input(snippet: str) -> None:
    with pytest.raises(management.ManagementError):
        management.parse_join_info(snippet)


# ---------------------------------------------------------------------------
# join


@pytest.mark.django_db
def test_join_success_pins_records_and_enables_site(
    node_fp: str,
    operator: User,
    auth_on: None,
    controller_reply: dict[str, Any],
    stub_node_and_caddy: MagicMock,
) -> None:
    from anthias_server.settings import settings

    controller = management.join(_snippet(), ' screen-1.lan ')

    assert controller_reply['pins'] == [CONTROLLER_FP]
    (call,) = controller_reply['calls']
    assert call['url'] == 'https://controller.lan:8443/api/v2/controller/join'
    assert call['allow_redirects'] is False
    body = call['json']
    assert body['secret'] == 'c2VjcmV0LXNlY3JldA'
    assert body['screen_fp'] == node_fp
    assert body['screen_port'] == 8443
    assert body['screen_host'] == 'screen-1.lan'
    assert body['screen_name'] == f'Anthias {node_fp[:8]}'

    # The token sent to the controller is a working bearer credential,
    # and only its hash is stored here.
    token = api_tokens.authenticate_token(body['token'])
    assert token is not None
    assert token.name == 'Controller: Lobby controller'
    assert body['token'] not in {token.token_hash, token.prefix}
    assert controller.token == token
    assert controller.name == 'Lobby'
    assert controller.url == 'https://controller.lan:8443'
    assert controller.fingerprint == CONTROLLER_FP

    # The request carries the node certificate and a CSR for its key.
    from anthias_server.lib import node_identity, pki

    cert = pki.load_cert(body['screen_cert'])
    assert pki.fingerprint(cert) == node_fp
    csr = pki.load_csr(body['csr'])
    assert pki.same_public_key(csr.public_key(), cert.public_key())
    assert node_identity.key_path().exists()

    # Waiting for approval: not managed yet, but the site is open so
    # the controller can call back.
    assert not controller.approved
    assert controller.request_id == 'rq-1'
    assert not management.is_managed()
    assert management.pending_controller() == controller
    assert settings['management_enabled'] is True
    assert management.m2m_site_active()
    assert stub_node_and_caddy.called

    _approve(controller)
    controller.refresh_from_db()
    assert controller.approved
    assert management.is_managed()
    assert management.pending_controller() is None
    assert management.client_cert_path().exists()
    assert management.controller_ca_path().exists()
    assert management.crl_path().exists()


@pytest.mark.django_db
def test_join_enables_site_before_contacting_controller(
    operator: User,
    auth_on: None,
    controller_reply: dict[str, Any],
) -> None:
    seen: list[dict[str, Any]] = []

    def reply() -> _FakeResponse:
        seen.append(management.caddy_extra_servers())
        return _FakeResponse(202, {'controller_name': 'Lobby'})

    controller_reply['outcome'] = reply
    management.join(_snippet())
    assert seen and management.M2M_SERVER_NAME in seen[0]


@pytest.mark.django_db
def test_join_omits_screen_host_when_blank(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    management.join(_snippet(), '   ')
    assert 'screen_host' not in controller_reply['calls'][0]['json']


@pytest.mark.django_db
def test_join_refused_without_auth(
    operator: User,
    _isolated_settings_conf: None,
    controller_reply: dict[str, Any],
) -> None:
    from anthias_server.settings import settings

    settings['auth_backend'] = ''
    with pytest.raises(management.ManagementError, match='authentication'):
        management.join(_snippet())
    assert controller_reply['calls'] == []
    assert not ApiToken.objects.exists()


@pytest.mark.django_db
def test_join_refused_while_already_managed(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    _join_and_approve()
    with pytest.raises(management.ManagementError, match='Leave management'):
        management.join(_snippet(name='Other'))
    assert len(controller_reply['calls']) == 1
    assert api_tokens.active_tokens().count() == 1


@pytest.mark.parametrize(
    ('outcome', 'message'),
    [
        (requests.exceptions.SSLError('fingerprint mismatch'), 'certificate'),
        (requests.exceptions.ConnectionError('refused'), 'Could not reach'),
        (_FakeResponse(403, {'detail': 'Bad secret.'}), 'regenerated'),
        (
            _FakeResponse(502, {'detail': 'Could not reach the screen.'}),
            'Could not reach the screen.',
        ),
        (_FakeResponse(500), 'HTTP 500'),
    ],
)
@pytest.mark.django_db
def test_join_failure_rolls_back(
    operator: User,
    auth_on: None,
    controller_reply: dict[str, Any],
    outcome: Any,
    message: str,
) -> None:
    from anthias_server.settings import settings

    controller_reply['outcome'] = outcome
    with pytest.raises(management.ManagementError, match=message):
        management.join(_snippet())

    assert not api_tokens.active_tokens().exists()
    assert ApiToken.objects.filter(revoked_at__isnull=False).count() == 1
    assert not ManagementController.objects.exists()
    assert settings['management_enabled'] is False
    assert management.caddy_extra_servers() == {}


@pytest.mark.django_db
def test_join_keeps_request_when_202_body_unreadable(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    controller_reply['outcome'] = _FakeResponse(202)
    controller = management.join(_snippet())
    assert controller.name == 'Lobby controller'
    assert management.pending_controller() == controller
    assert not management.is_managed()


@pytest.mark.django_db
@pytest.mark.parametrize('host', ['has space', 'a/b', 'x' * 254])
def test_join_rejects_bad_screen_host(
    operator: User,
    auth_on: None,
    controller_reply: dict[str, Any],
    host: str,
) -> None:
    with pytest.raises(management.ManagementError, match='address'):
        management.join(_snippet(), host)
    assert not ApiToken.objects.exists()


# ---------------------------------------------------------------------------
# managed state and leaving


@pytest.mark.django_db
def test_revoking_controller_token_ends_management(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    controller = _join_and_approve()
    api_tokens.revoke_token(controller.token.pk)
    assert not management.is_managed()


@pytest.mark.django_db
def test_leave_management_revokes_and_withdraws_site(
    operator: User,
    auth_on: None,
    controller_reply: dict[str, Any],
    stub_node_and_caddy: MagicMock,
) -> None:
    from anthias_server.settings import settings

    _join_and_approve()
    plaintext = controller_reply['calls'][0]['json']['token']
    stub_node_and_caddy.reset_mock()

    assert management.leave_management() is True
    # The certificates from the controller go with it.
    assert not management.client_cert_path().exists()
    assert not management.controller_ca_path().exists()
    assert not management.crl_path().exists()

    assert api_tokens.authenticate_token(plaintext) is None
    assert not ManagementController.objects.exists()
    assert settings['management_enabled'] is False
    assert management.caddy_extra_servers() == {}
    stub_node_and_caddy.assert_called()
    assert management.leave_management() is False


def _pair(operator: User) -> ManagementController:
    token, _ = api_tokens.create_token(operator, 'Controller: Lobby')
    return ManagementController.objects.create(
        name='Lobby',
        url='https://controller.lan:8443',
        fingerprint=CONTROLLER_FP,
        token=token,
    )


@pytest.mark.django_db
def test_site_follows_auth_and_controller_mode(
    operator: User, auth_on: None
) -> None:
    from anthias_server.settings import settings

    assert management.caddy_extra_servers() == {}
    settings['management_enabled'] = True
    _pair(operator)
    assert management.M2M_SERVER_NAME in management.caddy_extra_servers()
    settings['auth_backend'] = ''
    assert management.caddy_extra_servers() == {}
    settings['auth_backend'] = 'auth_basic'
    settings['management_enabled'] = False
    settings['controller_mode'] = True
    assert management.M2M_SERVER_NAME in management.caddy_extra_servers()


@pytest.mark.django_db
def test_m2m_server_shape() -> None:
    server = management.m2m_server()
    assert server['listen'] == [':8443']
    assert server['automatic_https'] == {'disable': True}
    assert server['tls_connection_policies'] == [
        {
            'certificate_selection': {'any_tag': ['anthias-node']},
            # Not managed yet: only ask for a client certificate.
            'client_authentication': {'mode': 'request'},
        }
    ]
    join_route, api_route, fallback = server['routes']
    assert join_route['match'] == [{'path': ['/api/v2/controller/join']}]
    assert join_route['handle'][0] == {
        'handler': 'request_body',
        'max_size': management.JOIN_MAX_BODY_BYTES,
    }
    assert api_route['match'] == [{'path': ['/api/v2/*']}]
    limit, proxy = api_route['handle']
    assert limit == {
        'handler': 'request_body',
        'max_size': management.M2M_MAX_BODY_BYTES,
    }
    assert proxy['handler'] == 'reverse_proxy'
    assert proxy['upstreams'] == [{'dial': 'anthias-server:8080'}]
    assert proxy['headers']['request']['set'] == {
        'X-Anthias-Listener': ['m2m'],
        'X-Anthias-Client-Cert': [
            '{http.request.tls.client.certificate_der_base64}'
        ],
    }
    assert fallback == {
        'handle': [{'handler': 'static_response', 'status_code': 404}]
    }


def test_listener_value_is_a_secret_in_the_run_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from anthias_server.lib import caddy

    monkeypatch.setattr(caddy, 'RUN_DIR', tmp_path)
    management.reset_listener_cache()
    try:
        value = management.listener_value()
        assert value.startswith('m2m:') and len(value) > 30
        secret_file = tmp_path / 'm2m-listener.secret'
        assert secret_file.stat().st_mode & 0o777 == 0o600
        # Stable across calls and restarts (re-read from the file).
        management.reset_listener_cache()
        assert management.listener_value() == value
        proxy = management.m2m_server()['routes'][1]['handle'][1]
        assert proxy['headers']['request']['set'] == {
            'X-Anthias-Listener': [value],
            'X-Anthias-Client-Cert': [
                '{http.request.tls.client.certificate_der_base64}'
            ],
        }
        request = MagicMock()
        request.headers = {'X-Anthias-Listener': 'm2m'}
        assert not management.request_via_m2m(request)
        request.headers = {'X-Anthias-Listener': value}
        assert management.request_via_m2m(request)
    finally:
        management.reset_listener_cache()


@pytest.mark.django_db
def test_caddy_config_includes_m2m_site_only_while_active(
    operator: User, auth_on: None
) -> None:
    from anthias_server.lib import caddy
    from anthias_server.settings import settings

    management.register_caddy_provider()
    servers = caddy.build_config()['apps']['http']['servers']
    assert management.M2M_SERVER_NAME not in servers

    settings['management_enabled'] = True
    servers = caddy.build_config()['apps']['http']['servers']
    assert management.M2M_SERVER_NAME not in servers
    _pair(operator)
    servers = caddy.build_config()['apps']['http']['servers']
    assert servers[management.M2M_SERVER_NAME] == management.m2m_server()
    # The node certificate the m2m site selects is always loaded.
    tls = caddy.build_config()['apps']['tls']['certificates']['load_files']
    assert any(caddy.TAG_NODE in entry['tags'] for entry in tls)


def test_ui_sites_overwrite_the_listener_header() -> None:
    from anthias_server.lib import caddy

    request_headers = caddy.proxy_handler()['headers']['request']
    assert request_headers['set'] == {caddy.LISTENER_HEADER: ['ui']}
    assert request_headers['delete'] == [caddy.CLIENT_CERT_HEADER]


def test_request_via_m2m() -> None:
    request = MagicMock()
    request.headers = {'X-Anthias-Listener': 'm2m'}
    assert management.request_via_m2m(request)
    request.headers = {'X-Anthias-Listener': 'ui'}
    assert not management.request_via_m2m(request)
    request.headers = {}
    assert not management.request_via_m2m(request)


# ---------------------------------------------------------------------------
# API: /api/v2/pairing


@pytest.mark.django_db
def test_api_pairing_get_and_controller_delete(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    controller = management.join(_snippet())
    ca_cert, ca_key = _approve(controller)
    plaintext = controller_reply['calls'][0]['json']['token']
    client = Client()
    auth = {'Authorization': f'Bearer {plaintext}'}
    url = reverse('api:pairing_v2')

    # The controller's token only works through the :8443 listener,
    # where a managed screen also wants its controller's certificate.
    assert client.get(url, headers=auth).status_code == 403
    auth.update(_der_header(_controller_cert(ca_cert, ca_key)))

    response = client.get(url, headers=auth)
    assert response.status_code == 200
    assert response.json() == {'managed': True, 'controller_name': 'Lobby'}

    response = client.delete(url, headers=auth)
    assert response.status_code == 204
    assert not management.is_managed()

    # The token is gone with the pairing.
    assert client.get(url, headers=auth).status_code == 401


@pytest.mark.django_db
def test_api_pairing_get_unauthenticated_is_refused(auth_on: None) -> None:
    # The shared @authorized gate answers with a login redirect.
    response = Client().get(reverse('api:pairing_v2'))
    assert response.status_code in (302, 401, 403)
    assert b'managed' not in response.content


@pytest.mark.django_db
def test_api_pairing_delete_with_unrelated_token_is_refused(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    _join_and_approve()
    _, other = api_tokens.create_token(operator, 'Script')
    response = Client().delete(
        reverse('api:pairing_v2'), HTTP_AUTHORIZATION=f'Bearer {other}'
    )
    assert response.status_code == 403
    assert management.is_managed()


@pytest.mark.django_db
def test_api_pairing_delete_with_session_is_refused(
    operator: User, auth_on: None
) -> None:
    client = Client()
    client.force_login(operator)
    response = client.delete(reverse('api:pairing_v2'))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Settings UI


@pytest.mark.django_db
def test_settings_page_shows_join_form(operator: User, auth_on: None) -> None:
    client = Client()
    client.force_login(operator)
    response = client.get(reverse('anthias_app:settings'))
    assert response.status_code == 200
    assert b'id="btn-join-controller"' in response.content


@pytest.mark.django_db
def test_settings_page_explains_auth_requirement(
    operator: User, _isolated_settings_conf: None
) -> None:
    response = Client().get(reverse('anthias_app:settings'))
    assert response.status_code == 200
    assert b'btn-join-controller' not in response.content
    assert b'Multi-screen management needs authentication' in response.content


@pytest.mark.django_db
def test_settings_join_and_leave_flow(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    client = Client()
    client.force_login(operator)

    response = client.post(
        reverse('anthias_app:settings_management_join'),
        {'join_info': _snippet(), 'screen_host': ''},
        HTTP_HX_REQUEST='true',
    )
    assert response.status_code == 200
    assert b'Waiting for approval on Lobby' in response.content
    assert management.check_code().encode() in response.content
    assert b'btn-cancel-join' in response.content
    # The pending section refreshes itself until approved.
    assert b'hx-trigger="every 5s"' in response.content

    pending = management.pending_controller()
    assert pending is not None
    _approve(pending)
    response = client.get(reverse('anthias_app:settings_management_section'))
    assert b'Managed by Lobby' in response.content
    assert b'btn-leave-management' in response.content

    # Banner on every page while managed.
    page = client.get(reverse('anthias_app:settings'))
    assert b'managed-alert-title' in page.content

    response = client.post(
        reverse('anthias_app:settings_management_leave'),
        HTTP_HX_REQUEST='true',
    )
    assert response.status_code == 200
    assert b'btn-join-controller' in response.content
    assert not management.is_managed()
    page = client.get(reverse('anthias_app:settings'))
    assert b'managed-alert-title' not in page.content


@pytest.mark.django_db
def test_settings_join_error_keeps_input(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    controller_reply['outcome'] = _FakeResponse(403, {'detail': 'nope'})
    client = Client()
    client.force_login(operator)
    response = client.post(
        reverse('anthias_app:settings_management_join'),
        {'join_info': _snippet(), 'screen_host': '10.1.2.3'},
        HTTP_HX_REQUEST='true',
    )
    assert response.status_code == 200
    assert b'role="alert"' in response.content
    assert b'10.1.2.3' in response.content
    assert not management.is_managed()


@pytest.mark.django_db
def test_disabling_auth_withdraws_site(
    operator: User,
    auth_on: None,
    controller_reply: dict[str, Any],
    stub_node_and_caddy: MagicMock,
) -> None:
    _join_and_approve()
    stub_node_and_caddy.reset_mock()
    client = Client()
    client.force_login(operator)
    client.post(
        reverse('anthias_app:settings_save'),
        {'auth_backend': '', 'current_password': _PWD},
    )
    from anthias_server.settings import settings

    assert settings['auth_backend'] == ''
    assert management.caddy_extra_servers() == {}
    stub_node_and_caddy.assert_called()


@pytest.mark.parametrize(
    ('service', 'registered'), [('', True), ('viewer', False)]
)
def test_app_registers_caddy_provider_except_in_viewer(
    monkeypatch: pytest.MonkeyPatch, service: str, registered: bool
) -> None:
    from django.apps import apps

    calls: list[object] = []
    monkeypatch.setattr(
        'anthias_server.lib.caddy.register_server_provider', calls.append
    )
    monkeypatch.setenv('ANTHIAS_SERVICE', service)
    from anthias_server.app.apps import AnthiasAppConfig

    config = apps.get_app_config('anthias_app')
    assert isinstance(config, AnthiasAppConfig)
    config._register_caddy_provider()
    assert calls == ([management.caddy_extra_servers] if registered else [])


@pytest.mark.django_db
def test_device_name_prefers_player_name(auth_on: None, node_fp: str) -> None:
    from anthias_server.settings import settings

    settings['player_name'] = ''
    assert management.device_name() == f'Anthias {node_fp[:8]}'
    settings['player_name'] = '  Lobby left  '
    assert management.device_name() == 'Lobby left'


@pytest.mark.django_db
def test_revoking_controller_token_in_settings_leaves_management(
    operator: User,
    auth_on: None,
    controller_reply: dict[str, Any],
    stub_node_and_caddy: MagicMock,
) -> None:
    from anthias_server.settings import settings

    controller = _join_and_approve()
    stub_node_and_caddy.reset_mock()
    client = Client()
    client.force_login(operator)
    response = client.post(
        reverse(
            'anthias_app:settings_api_tokens_revoke',
            args=[controller.token.pk],
        ),
        HTTP_HX_REQUEST='true',
    )
    assert response.status_code == 200
    assert not management.is_managed()
    assert not ManagementController.objects.exists()
    assert settings['management_enabled'] is False
    assert management.caddy_extra_servers() == {}
    stub_node_and_caddy.assert_called()


@pytest.mark.django_db
def test_stale_flag_without_controller_keeps_site_closed(
    auth_on: None,
) -> None:
    # Found on the QA rig: anthias.conf still said
    # management_enabled after the pairing was gone, and :8443 came
    # back on the next server start. The flag alone must not open it.
    from anthias_server.settings import settings

    settings['management_enabled'] = True
    assert not management.is_managed()
    assert not management.m2m_site_active()
    assert management.caddy_extra_servers() == {}


@pytest.mark.django_db
def test_site_closes_when_token_revoked_by_any_path(
    operator: User, auth_on: None
) -> None:
    from anthias_server.settings import settings

    settings['management_enabled'] = True
    controller = _pair(operator)
    assert management.m2m_site_active()
    # Not through leave_management()/unpair_token(): straight to the
    # token, so the flag is left behind.
    api_tokens.revoke_token(controller.token.pk)
    assert settings['management_enabled'] is True
    assert not management.m2m_site_active()


@pytest.mark.django_db
def test_join_failure_does_not_leave_join_marker(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    from anthias_server.settings import settings

    controller_reply['outcome'] = lambda: _FakeResponse(403, {})
    with pytest.raises(management.ManagementError):
        management.join(_snippet())
    settings['management_enabled'] = True
    assert not management.m2m_site_active()


@pytest.mark.django_db
def test_leaving_rewrites_flag_changed_by_another_process(
    operator: User, auth_on: None
) -> None:
    # Found on the QA rig: the flag was written by another process,
    # so this process's in-memory copy still said False, the leave
    # skipped the write, and anthias.conf kept management_enabled on.
    import configparser

    from anthias_server.settings import settings

    _pair(operator)
    parser = configparser.ConfigParser()
    parser.read(settings.conf_file)
    parser['main']['management_enabled'] = 'on'
    with open(settings.conf_file, 'w') as handle:
        parser.write(handle)
    assert settings['management_enabled'] is False  # stale copy

    assert management.leave_management() is True

    parser = configparser.ConfigParser()
    parser.read(settings.conf_file)
    assert parser['main'].getboolean('management_enabled') is False


# ---------------------------------------------------------------------------
# approval, certificates and revocation (screen side)


def _controller_cert(
    ca_cert: Any, ca_key: Any, role: str = 'anthias-controller'
) -> Any:
    from anthias_server.lib import pki

    return pki.issue_client_cert(
        ca_cert, ca_key, pki.new_key().public_key(), 'controller', role
    )


def _der_header(cert: Any) -> dict[str, str]:
    from cryptography.hazmat.primitives import serialization

    der = cert.public_bytes(serialization.Encoding.DER)
    return {
        'X-Anthias-Listener': 'm2m',
        'X-Anthias-Client-Cert': base64.b64encode(der).decode(),
    }


@pytest.mark.django_db
def test_settings_show_the_check_code_while_waiting(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    management.join(_snippet())
    client = Client()
    client.force_login(operator)
    html = client.get(
        reverse('anthias_app:settings_management_section')
    ).content.decode()
    assert 'Waiting for approval' in html
    assert management.check_code() in html
    assert 'btn-cancel-join' in html


@pytest.mark.django_db
def test_controller_delivers_the_certificate_over_the_api(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    from anthias_server.lib import node_identity, pki

    controller = management.join(_snippet())
    plaintext = controller_reply['calls'][0]['json']['token']
    ca_key = pki.new_key()
    ca_cert = pki.make_ca(ca_key, 'Test controller CA')
    node_key = pki.load_key(
        node_identity.ensure_node_identity().key_path.read_bytes()
    )
    cert = pki.issue_client_cert(
        ca_cert, ca_key, node_key.public_key(), 'screen', pki.ROLE_SCREEN
    )
    body = {
        'certificate': pki.cert_pem(cert),
        'ca_certificate': pki.cert_pem(ca_cert),
        'crl': pki.crl_pem(pki.make_crl(ca_cert, ca_key, [], 1)),
        'controller_name': 'Head office',
    }
    url = reverse('api:pairing_certificate_v2')
    auth = {'Authorization': f'Bearer {plaintext}'}
    client = Client()

    # Only on the management port, and only with the controller token
    # (which is itself refused off the management port).
    assert (
        client.post(
            url, body, content_type='application/json', headers=auth
        ).status_code
        == 403
    )
    session = Client()
    session.force_login(operator)
    assert (
        session.post(
            url,
            body,
            content_type='application/json',
            headers={'X-Anthias-Listener': 'm2m'},
        ).status_code
        == 403
    )
    bad = {**body, 'certificate': pki.cert_pem(ca_cert)}
    response = client.post(
        url,
        bad,
        content_type='application/json',
        headers={**auth, 'X-Anthias-Listener': 'm2m'},
    )
    assert response.status_code == 400
    assert not management.is_managed()

    response = client.post(
        url,
        body,
        content_type='application/json',
        headers={**auth, 'X-Anthias-Listener': 'm2m'},
    )
    assert response.status_code == 204, response.content
    controller.refresh_from_db()
    assert controller.approved
    assert controller.name == 'Head office'
    assert management.is_managed()


@pytest.mark.django_db
def test_install_approval_refuses_what_does_not_check_out(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    from anthias_server.lib import node_identity, pki

    controller = management.join(_snippet())
    ca_key = pki.new_key()
    ca_cert = pki.make_ca(ca_key, 'CA')
    node_key = pki.load_key(
        node_identity.ensure_node_identity().key_path.read_bytes()
    )
    good = pki.issue_client_cert(
        ca_cert, ca_key, node_key.public_key(), 'screen', pki.ROLE_SCREEN
    )
    empty_crl = pki.make_crl(ca_cert, ca_key, [], 1)
    cases = {
        'another key': (
            pki.issue_client_cert(
                ca_cert,
                ca_key,
                pki.new_key().public_key(),
                'screen',
                pki.ROLE_SCREEN,
            ),
            ca_cert,
            empty_crl,
        ),
        'controller role': (
            _controller_cert(ca_cert, ca_key),
            ca_cert,
            empty_crl,
        ),
        'not a CA': (good, good, empty_crl),
        'already revoked': (
            good,
            ca_cert,
            pki.make_crl(
                ca_cert, ca_key, [(good.serial_number, pki.now())], 2
            ),
        ),
    }
    for name, (cert, authority, crl) in cases.items():
        pems = (pki.cert_pem(cert), pki.cert_pem(authority), pki.crl_pem(crl))
        with pytest.raises(management.ManagementError):
            management.install_approval(controller.token_id, *pems)
        assert not management.is_managed(), name
    good_pems = (
        pki.cert_pem(good),
        pki.cert_pem(ca_cert),
        pki.crl_pem(empty_crl),
    )
    with pytest.raises(management.ManagementError):
        management.install_approval(controller.token_id + 1000, *good_pems)


@pytest.mark.django_db
def test_a_managed_screen_accepts_m2m_calls_only_from_its_controller(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    from anthias_server.lib import pki

    controller = management.join(_snippet())
    ca_cert, ca_key = _approve(controller)
    plaintext = controller_reply['calls'][0]['json']['token']
    auth = {'Authorization': f'Bearer {plaintext}'}
    url = reverse('api:pairing_v2')
    good = _controller_cert(ca_cert, ca_key)

    def status(headers: dict[str, str]) -> int:
        return int(Client().get(url, headers={**auth, **headers}).status_code)

    assert status(_der_header(good)) == 200
    # Off the management port the controller's token is refused outright.
    assert status({}) == 403
    assert status({'X-Anthias-Listener': 'm2m'}) == 403
    assert status({**_der_header(good), 'X-Anthias-Client-Cert': '!!'}) == 403
    assert (
        status(_der_header(_controller_cert(ca_cert, ca_key, pki.ROLE_SCREEN)))
        == 403
    )
    other_key = pki.new_key()
    other = pki.make_ca(other_key, 'Other')
    assert status(_der_header(_controller_cert(other, other_key))) == 403

    # Revoked: a newer CRL from the controller lists it.
    crl = pki.make_crl(ca_cert, ca_key, [(good.serial_number, pki.now())], 5)
    assert management.store_crl(pki.crl_pem(crl))
    assert status(_der_header(good)) == 403
    # An older CRL does not undo that; one from another CA is ignored.
    stale = pki.make_crl(ca_cert, ca_key, [], 4)
    assert not management.store_crl(pki.crl_pem(stale))
    foreign = pki.make_crl(other, other_key, [], 9)
    assert not management.store_crl(pki.crl_pem(foreign))
    assert status(_der_header(good)) == 403


@pytest.mark.django_db
def test_a_renewed_certificate_is_kept_only_if_it_is_ours(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    from anthias_server.lib import node_identity, pki

    controller = management.join(_snippet())
    ca_cert, ca_key = _approve(controller)
    node_key = pki.load_key(
        node_identity.ensure_node_identity().key_path.read_bytes()
    )
    before = management.client_cert_path().read_text()
    foreign = pki.issue_client_cert(
        ca_cert, ca_key, pki.new_key().public_key(), 's', pki.ROLE_SCREEN
    )
    assert not management.store_client_cert(pki.cert_pem(foreign))
    assert management.client_cert_path().read_text() == before
    renewed = pki.issue_client_cert(
        ca_cert, ca_key, node_key.public_key(), 's', pki.ROLE_SCREEN
    )
    assert management.store_client_cert(pki.cert_pem(renewed))
    assert management.client_cert_path().read_text() == pki.cert_pem(renewed)


@pytest.mark.django_db
def test_leaving_deletes_the_controller_certificates(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    _join_and_approve()
    management.leave_management()
    for path in (
        management.client_cert_path(),
        management.controller_ca_path(),
        management.crl_path(),
    ):
        assert not path.exists()


@pytest.mark.django_db
def test_a_controller_cannot_join_another_controller(
    auth_on: None, controller_reply: dict[str, Any]
) -> None:
    from anthias_server.settings import settings

    settings['controller_mode'] = True
    settings.save()
    snippet = _snippet()
    with pytest.raises(management.ManagementError):
        management.join(snippet)
    assert controller_reply['calls'] == []


# ---------------------------------------------------------------------------
# a managed screen's web interface is shut


UI = {'X-Anthias-Listener': 'ui'}


@pytest.fixture
def signed_in(operator: User) -> Client:
    client = Client()
    client.force_login(operator)
    return client


@pytest.mark.django_db
def test_a_managed_screen_shows_only_the_managed_page(
    signed_in: Client, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    _join_and_approve()
    for path in ('/', '/settings/', '/system-info/', '/integrations/'):
        response = signed_in.get(path, headers=UI)
        assert response.status_code == 302, path
        assert response['Location'] == reverse('anthias_app:managed')
    response = signed_in.post(
        reverse('anthias_app:settings_save'), {}, headers=UI
    )
    assert response['Location'] == reverse('anthias_app:managed')

    html = signed_in.get(
        reverse('anthias_app:managed'), headers=UI
    ).content.decode()
    assert 'Managed by Lobby' in html
    # A link to the controller's web interface, not its 8443 port.
    assert 'href="https://controller.lan/screens/"' in html
    assert 'controller.lan:8443' not in html
    assert 'btn-managed-leave' in html
    # Signing in stays possible.
    assert Client().get('/login/', headers=UI).status_code == 200


@pytest.mark.django_db
def test_a_managed_screen_refuses_its_local_api(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    _join_and_approve()
    plaintext = controller_reply['calls'][0]['json']['token']
    auth = {'Authorization': f'Bearer {plaintext}'}
    response = Client().get('/api/v2/assets', headers={**auth, **UI})
    assert response.status_code == 403
    assert 'managed' in response.json()['detail']


@pytest.mark.django_db
@pytest.mark.parametrize(
    'listener',
    [
        None,  # the viewer / webview, inside the Docker network
        'm2m',  # the controller, on 8443
    ],
)
def test_only_the_web_interface_is_shut(
    signed_in: Client,
    auth_on: None,
    controller_reply: dict[str, Any],
    listener: str | None,
) -> None:
    from django.http import HttpResponse
    from django.test import RequestFactory

    _join_and_approve()
    headers = {'X-Anthias-Listener': listener} if listener else {}
    middleware = management.ManagedUILockdownMiddleware(
        lambda _request: HttpResponse('through')
    )
    for path in ('/', '/api/v2/assets', '/settings/'):
        request = RequestFactory().get(path, headers=headers)
        assert middleware(request).content == b'through', path
    if listener is None:
        assert signed_in.get('/').status_code == 200


@pytest.mark.django_db
def test_waiting_for_approval_keeps_the_web_interface(
    signed_in: Client, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    management.join(_snippet())
    assert not management.ui_locked()
    assert signed_in.get('/settings/', headers=UI).status_code == 200
    # Nothing to show on the managed page yet.
    response = signed_in.get(reverse('anthias_app:managed'), headers=UI)
    assert response['Location'] == reverse('anthias_app:home')


@pytest.mark.django_db
def test_leaving_from_the_managed_page_opens_the_web_interface(
    signed_in: Client, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    _join_and_approve()
    response = signed_in.post(reverse('anthias_app:managed_leave'), headers=UI)
    assert response['Location'] == reverse('anthias_app:home')
    assert not management.is_managed()
    assert not management.ui_locked()
    assert signed_in.get('/', headers=UI).status_code == 200


@pytest.mark.django_db
def test_leaving_from_the_managed_page_needs_a_sign_in(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    _join_and_approve()
    response = Client().post(reverse('anthias_app:managed_leave'), headers=UI)
    assert response.status_code == 302
    assert reverse('anthias_app:login') in response['Location']
    assert management.is_managed()


# ---------------------------------------------------------------------------
# the warning while authentication is off


@pytest.mark.django_db
def test_every_page_warns_while_authentication_is_off(
    _isolated_settings_conf: None,
) -> None:
    from anthias_server.settings import settings

    settings['auth_backend'] = ''
    settings.save()
    for path in ('/', '/settings/', '/system-info/'):
        html = Client().get(path).content.decode()
        assert 'id="auth-warning"' in html, path
        assert 'Anyone on your network can change this screen' in html


@pytest.mark.django_db
def test_no_warning_once_authentication_is_on(
    signed_in: Client, auth_on: None
) -> None:
    html = signed_in.get('/').content.decode()
    assert 'id="auth-warning"' not in html


@pytest.mark.django_db
def test_sign_in_on_a_managed_screen_says_who_manages_it(
    operator: User, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    _join_and_approve()
    html = Client().get('/login/', headers=UI).content.decode()
    assert 'Managed by Lobby' in html
    assert 'href="https://controller.lan/screens/"' in html
    # An unmanaged screen keeps the usual greeting.
    management.leave_management()
    assert 'Welcome back' in Client().get('/login/').content.decode()


def test_controller_web_url_points_at_its_web_interface() -> None:
    from types import SimpleNamespace

    def url(value: str) -> str:
        return management.controller_web_url(SimpleNamespace(url=value))

    assert url('https://10.0.0.5:8443') == 'https://10.0.0.5/screens/'
    assert url('https://[fd00::5]:8443') == 'https://[fd00::5]/screens/'
    assert url('https://ctl.lan:8443/') == 'https://ctl.lan/screens/'


@pytest.mark.django_db
def test_leaving_from_the_managed_page_asks_first(
    signed_in: Client, auth_on: None, controller_reply: dict[str, Any]
) -> None:
    _join_and_approve()
    html = signed_in.get(
        reverse('anthias_app:managed'), headers=UI
    ).content.decode()
    form = html.split('id="btn-managed-leave"')[0].rsplit('<form', 1)[1]
    assert 'confirm(' in form
