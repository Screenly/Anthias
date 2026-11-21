"""Controller mode: join information, the join endpoint, the Screens page."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from anthias_server.controller import join, service, tasks, vault
from anthias_server.controller.client import NodeOfflineError
from anthias_server.controller.models import (
    AuditEntry,
    JoinRequest,
    JoinSecret,
    Node,
    NodeStatus,
    PublishState,
)
from anthias_server.lib import management, pki

_PWD = 'fixture-controller-operator-pwd'  # NOSONAR
CONTROLLER_FP = 'c' * 64
# A real screen identity: joining needs its certificate and a CSR for
# the same key, and the fingerprint must be that certificate's.
_SCREEN_KEY = pki.new_key()
_SCREEN_CERT = pki.make_ca(_SCREEN_KEY, 'test screen')
SCREEN_FP = pki.fingerprint(_SCREEN_CERT)
_SCREEN_CSR = pki.make_csr(_SCREEN_KEY, SCREEN_FP, pki.ROLE_SCREEN)
M2M = {'X-Anthias-Listener': 'm2m'}
JOIN_URL = '/api/v2/controller/join'


@pytest.fixture(autouse=True)
def _vault_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    monkeypatch.setattr(vault, 'key_path', lambda: str(tmp_path / 'k.key'))
    vault.reset_cache()
    yield
    vault.reset_cache()


@pytest.fixture(autouse=True)
def _identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(management, 'node_fingerprint', lambda: CONTROLLER_FP)
    monkeypatch.setattr(management, 'sync_state', lambda: None)


@pytest.fixture
def controller_on(_isolated_settings_conf: None) -> Iterator[None]:
    from anthias_server.settings import settings

    settings['auth_backend'] = 'auth_basic'
    settings['controller_mode'] = True
    settings['player_name'] = 'Head office'
    settings.save()
    yield


@pytest.fixture
def operator() -> User:
    return User.objects.create_superuser(username='operator', password=_PWD)


@pytest.fixture
def operator_client(operator: User) -> Client:
    client = Client()
    client.force_login(operator)
    return client


@pytest.fixture
def screen_ok(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []

    def fake_verify(request: join.JoinRequest) -> dict[str, Any]:
        calls.append(request)
        return {'anthias_version': 'v2026.11.2', 'device_model': 'Pi 4'}

    monkeypatch.setattr(join, 'verify_screen', fake_verify)
    return calls


def _body(secret: str, **extra: Any) -> dict[str, Any]:
    body = {
        'secret': secret,
        'token': 'anthias_screen_token',
        'screen_name': 'Lobby',
        'screen_fp': SCREEN_FP,
        'screen_port': 8443,
        'screen_cert': pki.cert_pem(_SCREEN_CERT),
        'csr': _SCREEN_CSR,
    }
    body.update(extra)
    return body


def _decode(snippet: str) -> dict[str, Any]:
    assert snippet.startswith('anthias-join:')
    raw = snippet.removeprefix('anthias-join:')
    raw += '=' * (-len(raw) % 4)
    data: dict[str, Any] = json.loads(base64.urlsafe_b64decode(raw))
    return data


# ---------------------------------------------------------------------------
# join information


@pytest.mark.django_db
def test_snippet_carries_url_fingerprint_secret_and_name() -> None:
    data = _decode(join.build_snippet('192.168.1.10', 'Head office'))
    assert data == {
        'v': 1,
        'url': 'https://192.168.1.10:8443',
        'fp': CONTROLLER_FP,
        'secret': join.current_secret(),
        'name': 'Head office',
    }
    assert '=' not in join.build_snippet('h', 'n')


@pytest.mark.django_db
def test_snippet_round_trips_through_the_screen_parser() -> None:
    snippet = join.build_snippet('ctl.example.lan', 'Head office')
    info = management.parse_join_info(snippet)
    assert info.fingerprint == CONTROLLER_FP
    assert info.url == 'https://ctl.example.lan:8443'


def test_ipv6_controller_url_is_bracketed() -> None:
    assert join.controller_url('fd00::1') == 'https://[fd00::1]:8443'


@pytest.mark.django_db
def test_secret_is_stable_until_regenerated_and_stored_encrypted() -> None:
    first = join.current_secret()
    assert join.current_secret() == first
    stored = JoinSecret.objects.get().ciphertext
    assert first not in stored
    second = join.regenerate_secret()
    assert second != first
    assert join.check_secret(second)
    assert not join.check_secret(first)


# ---------------------------------------------------------------------------
# POST /api/v2/controller/join


@pytest.mark.django_db
def test_join_stores_the_screen_after_calling_it_back(
    controller_on: None, screen_ok: list[Any]
) -> None:
    secret = join.current_secret()
    # A machine client: no session, no CSRF token.
    response = Client(enforce_csrf_checks=True).post(
        JOIN_URL,
        _body(secret, screen_host='10.0.0.7'),
        content_type='application/json',
        headers=M2M,
    )
    assert response.status_code == 202, response.content
    # Listed for approval; nothing is trusted yet.
    assert not Node.objects.exists()
    pending = JoinRequest.objects.get()
    body = response.json()
    assert body['status'] == 'pending'
    assert body['request_id'] == pending.request_id
    assert body['check_code'] == pki.check_code(SCREEN_FP)
    assert body['controller_name'] == 'Head office'
    assert (pending.name, pending.host, pending.port) == (
        'Lobby',
        '10.0.0.7',
        8443,
    )
    assert pending.fingerprint == SCREEN_FP
    assert pending.version == 'v2026.11.2'
    assert 'anthias_screen_token' not in pending.token_ciphertext
    assert vault.decrypt(pending.token_ciphertext) == 'anthias_screen_token'
    assert pending.csr_pem == _SCREEN_CSR.strip()
    assert screen_ok[0].screen_fp == SCREEN_FP
    assert AuditEntry.objects.get(action='join request').outcome == 'ok'


@pytest.mark.django_db
def test_join_defaults_the_host_to_the_caller_address(
    controller_on: None, screen_ok: list[Any]
) -> None:
    Client().post(
        JOIN_URL,
        _body(join.current_secret()),
        content_type='application/json',
        headers=M2M,
        REMOTE_ADDR='192.168.1.50',
    )
    assert JoinRequest.objects.get().host == '192.168.1.50'


@pytest.mark.django_db
def test_screen_without_a_name_is_listed_by_address(
    controller_on: None, screen_ok: list[Any]
) -> None:
    Client().post(
        JOIN_URL,
        _body(join.current_secret(), screen_name='02706ec0e872'),
        content_type='application/json',
        headers=M2M,
        REMOTE_ADDR='192.168.1.51',
    )
    assert JoinRequest.objects.get().name == '192.168.1.51'


def test_controller_name_falls_back_to_its_address(
    _isolated_settings_conf: None,
) -> None:
    assert service.controller_name('192.168.1.10') == 'Anthias at 192.168.1.10'


@pytest.mark.django_db
def test_join_with_wrong_secret_is_refused(
    controller_on: None, screen_ok: list[Any]
) -> None:
    join.current_secret()
    response = Client().post(
        JOIN_URL,
        _body('not-the-secret'),
        content_type='application/json',
        headers=M2M,
    )
    assert response.status_code == 403
    assert 'join information' in response.json()['detail']
    assert screen_ok == []
    assert not Node.objects.exists()
    assert not JoinRequest.objects.exists()


@pytest.mark.django_db
def test_join_only_on_the_m2m_listener(
    controller_on: None, screen_ok: list[Any]
) -> None:
    response = Client().post(
        JOIN_URL,
        _body(join.current_secret()),
        content_type='application/json',
    )
    assert response.status_code == 404
    assert not Node.objects.exists()


@pytest.mark.django_db
def test_join_refused_when_controller_mode_is_off(
    _isolated_settings_conf: None, screen_ok: list[Any]
) -> None:
    response = Client().post(
        JOIN_URL,
        _body(join.current_secret()),
        content_type='application/json',
        headers=M2M,
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_join_reports_an_unreachable_screen_and_stores_nothing(
    controller_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from anthias_server.controller import client as client_mod

    def offline(*_a: Any, **_k: Any) -> Any:
        raise NodeOfflineError('connection refused')

    monkeypatch.setattr(client_mod.NodeClient, 'json', offline)
    response = Client().post(
        JOIN_URL,
        _body(join.current_secret(), screen_host='10.0.0.9'),
        content_type='application/json',
        headers=M2M,
    )
    assert response.status_code == 502
    assert 'could not reach this screen back' in response.json()['detail']
    assert not Node.objects.exists()
    assert not JoinRequest.objects.exists()


@pytest.mark.django_db
def test_rejoin_replaces_token_and_address(
    controller_on: None, screen_ok: list[Any]
) -> None:
    secret = join.current_secret()
    for host, token in (
        ('10.0.0.7', 'anthias_one'),
        ('10.0.0.8', 'anthias_two'),
    ):
        Client().post(
            JOIN_URL,
            _body(secret, screen_host=host, token=token),
            content_type='application/json',
            headers=M2M,
        )
    pending = JoinRequest.objects.get()
    assert pending.host == '10.0.0.8'
    assert vault.decrypt(pending.token_ciphertext) == 'anthias_two'


@pytest.mark.django_db
@pytest.mark.parametrize(
    'override',
    [
        {'screen_fp': 'nothex'},
        {'token': ''},
        {'screen_port': 70000},
        {'screen_port': '8443'},
        {'screen_host': 'bad host!'},
    ],
)
def test_join_validates_the_body(
    controller_on: None, screen_ok: list[Any], override: dict[str, Any]
) -> None:
    response = Client().post(
        JOIN_URL,
        _body(
            join.current_secret(), **{'screen_host': '10.0.0.7', **override}
        ),
        content_type='application/json',
        headers=M2M,
    )
    assert response.status_code == 400
    assert screen_ok == []


@pytest.mark.django_db
def test_join_is_rate_limited(controller_on: None) -> None:
    with patch.object(join, 'rate_limited', return_value=True):
        response = Client().post(
            JOIN_URL,
            _body('x'),
            content_type='application/json',
            headers=M2M,
        )
    assert response.status_code == 429


def test_rate_limit_counts_per_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts: dict[str, int] = {}

    class FakeRedis:
        def incr(self, key: str) -> int:
            counts[key] = counts.get(key, 0) + 1
            return counts[key]

        def expire(self, key: str, ttl: int) -> bool:
            return True

    monkeypatch.setattr(
        'anthias_common.utils.connect_to_redis', lambda: FakeRedis()
    )
    results = [join.rate_limited('1.2.3.4') for _ in range(11)]
    assert results == [False] * 10 + [True]
    assert not join.rate_limited('5.6.7.8')


# ---------------------------------------------------------------------------
# Screens page


@pytest.mark.django_db
def test_screens_page_explains_controller_mode_when_off(
    operator_client: Client, _isolated_settings_conf: None
) -> None:
    body = operator_client.get(reverse('controller:screens')).content.decode()
    assert 'Controller mode is off' in body
    assert 'join-snippet' not in body


@pytest.mark.django_db
def test_screens_page_shows_join_information_and_screens(
    operator_client: Client, controller_on: None
) -> None:
    Node.objects.create(
        name='Lobby',
        host='10.0.0.7',
        fingerprint=SCREEN_FP,
        token_ciphertext=vault.encrypt('anthias_x'),
        status=NodeStatus.ONLINE,
    )
    body = operator_client.get(
        reverse('controller:screens'), HTTP_HOST='192.168.1.10'
    ).content.decode()
    assert 'id="join-snippet"' in body
    snippet = body.split('id="join-snippet"')[1].split('>')[1].split('<')[0]
    assert _decode(snippet.strip())['url'] == 'https://192.168.1.10:8443'
    assert 'Lobby' in body
    assert 'This screen' in body
    assert 'anthias_x' not in body
    nav = operator_client.get(reverse('anthias_app:settings')).content
    assert reverse('controller:screens').encode() in nav


@pytest.mark.django_db
def test_publish_all_enqueues_one_task_per_active_screen(
    operator_client: Client, controller_on: None
) -> None:
    active = Node.objects.create(
        name='A', host='a', fingerprint='1' * 64, token_ciphertext='x'
    )
    Node.objects.create(
        name='Gone',
        host='b',
        fingerprint='2' * 64,
        token_ciphertext='x',
        status=NodeStatus.UNAUTHORIZED,
    )
    with patch.object(tasks.publish_to_node, 'delay') as delay:
        response = operator_client.post(
            reverse('controller:screens_publish'), {'target': 'all'}
        )
    assert response.status_code == 302
    delay.assert_called_once_with(active.pk, 'operator')
    active.refresh_from_db()
    assert active.publish_state == PublishState.QUEUED


@pytest.mark.django_db
def test_bulk_command_fans_out_and_includes_this_screen(
    operator_client: Client, controller_on: None
) -> None:
    node = Node.objects.create(
        name='A', host='a', fingerprint='1' * 64, token_ciphertext='x'
    )
    with (
        patch.object(tasks.node_command, 'delay') as delay,
        patch.object(service, 'run_local_command') as local,
    ):
        operator_client.post(
            reverse('controller:screens_command'),
            {'target': 'all', 'command': 'next'},
        )
        operator_client.post(
            reverse('controller:screens_command'),
            {'target': 'all', 'command': 'reboot'},
        )
    assert delay.call_args_list[0].args == (node.pk, 'next', 'operator')
    assert delay.call_args_list[1].args == (node.pk, 'reboot', 'operator')
    # A bulk reboot never reboots the controller itself.
    local.assert_called_once_with('next', 'operator')


@pytest.mark.django_db
def test_unknown_command_is_refused(
    operator_client: Client, controller_on: None
) -> None:
    with patch.object(tasks.node_command, 'delay') as delay:
        operator_client.post(
            reverse('controller:screens_command'),
            {'target': 'all', 'command': 'shutdown'},
        )
    delay.assert_not_called()


@pytest.mark.django_db
def test_remove_revokes_on_the_screen_and_forgets_it(
    operator_client: Client,
    controller_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = Node.objects.create(
        name='A',
        host='a',
        fingerprint='1' * 64,
        token_ciphertext=vault.encrypt('anthias_x'),
    )
    calls: list[tuple[str, str]] = []

    def fake_request(self: Any, method: str, path: str, **_: Any) -> Any:
        calls.append((method, path))

    from anthias_server.controller import client as client_mod

    monkeypatch.setattr(client_mod.NodeClient, 'request', fake_request)
    operator_client.post(reverse('controller:screens_remove', args=[node.pk]))
    assert calls == [('DELETE', '/pairing')]
    assert not Node.objects.exists()
    assert AuditEntry.objects.get(action='remove').outcome == 'ok'


@pytest.mark.django_db
def test_remove_unreachable_screen_still_forgets_it(
    operator_client: Client,
    controller_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = Node.objects.create(
        name='A',
        host='a',
        fingerprint='1' * 64,
        token_ciphertext=vault.encrypt('anthias_x'),
    )

    def offline(*_a: Any, **_k: Any) -> Any:
        raise NodeOfflineError('timed out')

    from anthias_server.controller import client as client_mod

    monkeypatch.setattr(client_mod.NodeClient, 'request', offline)
    operator_client.post(reverse('controller:screens_remove', args=[node.pk]))
    assert not Node.objects.exists()
    assert AuditEntry.objects.get(action='remove').outcome == 'partial'


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('auth_enabled', 'controller_mode'),
    [(False, False), (False, True), (True, False)],
)
def test_remove_requires_an_enabled_controller(
    operator_client: Client,
    controller_on: None,
    auth_enabled: bool,
    controller_mode: bool,
) -> None:
    from anthias_server.settings import settings

    node = Node.objects.create(
        name='Lobby',
        host='screen',
        fingerprint=SCREEN_FP,
        token_ciphertext='x',
    )
    settings['auth_backend'] = 'auth_basic' if auth_enabled else ''
    settings['controller_mode'] = controller_mode
    settings.save()
    client = operator_client if auth_enabled else Client()
    with patch.object(service, 'remove_node') as remove:
        response = client.post(
            reverse('controller:screens_remove', args=[node.pk])
        )
    assert response.status_code == 302
    remove.assert_not_called()
    assert Node.objects.filter(pk=node.pk).exists()


@pytest.mark.django_db
def test_screens_recovers_a_join_secret_from_a_foreign_backup(
    operator_client: Client, controller_on: None
) -> None:
    foreign_secret = 'secret-from-another-controller'
    ciphertext = (
        Fernet(Fernet.generate_key()).encrypt(foreign_secret.encode()).decode()
    )
    JoinSecret.objects.create(ciphertext=ciphertext)

    response = operator_client.get(reverse('controller:screens'))

    assert response.status_code == 200
    secret = _decode(response.context['join_snippet'])['secret']
    assert secret != foreign_secret
    assert join.check_secret(secret)
    assert not join.check_secret(foreign_secret)
    assert join.current_secret() == secret
    assert JoinSecret.objects.count() == 1


@pytest.mark.django_db
def test_regenerate_changes_the_secret(
    operator_client: Client, controller_on: None
) -> None:
    before = join.current_secret()
    operator_client.post(reverse('controller:screens_regenerate'))
    assert join.current_secret() != before


@pytest.mark.django_db
def test_settings_refuse_controller_mode_without_auth(
    operator_client: Client, _isolated_settings_conf: None
) -> None:
    from anthias_server.settings import settings

    operator_client.post(
        reverse('anthias_app:settings_save'),
        {'controller_mode': 'true', 'auth_backend': ''},
    )
    settings.load()
    assert settings['controller_mode'] is False


@pytest.mark.django_db
def test_status_poll_skips_screens_that_left(
    controller_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    checked: list[str] = []

    def fake_check(node: Node) -> Node:
        checked.append(node.name)
        return node

    monkeypatch.setattr(service, 'check_node', fake_check)
    Node.objects.create(
        name='A', host='a', fingerprint='1' * 64, token_ciphertext='x'
    )
    Node.objects.create(
        name='Gone',
        host='b',
        fingerprint='2' * 64,
        token_ciphertext='x',
        status=NodeStatus.UNAUTHORIZED,
    )
    assert tasks.refresh_nodes() == 1
    assert checked == ['A']


@pytest.mark.django_db
def test_status_poll_resumes_after_a_slow_screen_hits_the_task_limit(
    controller_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    nodes = [
        Node.objects.create(
            name=f'Screen {i}',
            host=f'screen-{i}',
            fingerprint=str(i) * 64,
            token_ciphertext='x',
        )
        for i in range(6)
    ]
    attempted: list[int] = []

    def interrupted_check(node: Node) -> Node:
        attempted.append(node.pk)
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(service, 'check_node', interrupted_check)
    for _ in nodes:
        with pytest.raises(SoftTimeLimitExceeded):
            tasks.refresh_nodes()

    assert attempted == [node.pk for node in nodes]
    assert not Node.objects.filter(last_checked_at__isnull=True).exists()


# ---------------------------------------------------------------------------
# approval, the CA and revocation


def _pending(**extra: Any) -> JoinRequest:
    response = Client().post(
        JOIN_URL,
        _body(join.current_secret(), screen_host='10.0.0.7', **extra),
        content_type='application/json',
        headers=M2M,
    )
    assert response.status_code == 202, response.content
    return JoinRequest.objects.get()


@pytest.fixture
def screen_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """What the controller sends the screen (NodeClient.request)."""
    calls: list[dict[str, Any]] = []

    def fake_request(self: Any, method: str, path: str, **kw: Any) -> Any:
        calls.append({'method': method, 'path': path, 'json': kw.get('json')})

    from anthias_server.controller import client as client_mod

    monkeypatch.setattr(client_mod.NodeClient, 'request', fake_request)
    return calls


@pytest.mark.django_db
def test_screens_page_lists_requests_with_their_check_code(
    operator_client: Client, controller_on: None, screen_ok: list[Any]
) -> None:
    pending = _pending()
    html = operator_client.get(reverse('controller:screens')).content.decode()
    assert 'Waiting for approval' in html
    assert pki.check_code(SCREEN_FP) in html
    assert f'btn-approve-{pending.pk}' in html
    assert f'btn-reject-{pending.pk}' in html


@pytest.mark.django_db
def test_approving_issues_a_certificate_for_the_screen_key(
    operator_client: Client,
    controller_on: None,
    screen_ok: list[Any],
    screen_calls: list[dict[str, Any]],
) -> None:
    from anthias_server.controller import ca

    pending = _pending()
    operator_client.post(
        reverse('controller:screens_approve', args=[pending.pk])
    )

    assert not JoinRequest.objects.exists()
    node = Node.objects.get()
    assert node.fingerprint == SCREEN_FP
    assert node.status == NodeStatus.ONLINE
    assert vault.decrypt(node.token_ciphertext) == 'anthias_screen_token'
    [call] = screen_calls
    assert (call['method'], call['path']) == ('POST', '/pairing/certificate')
    issued = pki.load_cert(call['json']['certificate'])
    authority = pki.load_cert(call['json']['ca_certificate'])
    assert pki.fingerprint(authority) == ca.ca_fingerprint()
    pki.check_client_cert(
        issued, authority, pki.ROLE_SCREEN, _SCREEN_KEY.public_key()
    )
    pki.check_crl(pki.load_crl(call['json']['crl']), authority)
    assert call['json']['controller_name'] == 'Head office'
    assert node.client_serial == pki.serial_hex(issued.serial_number)
    assert node.client_fingerprint == pki.fingerprint(issued)
    assert pki.same_public_key(
        pki.load_public_key(node.public_key_pem), _SCREEN_KEY
    )
    assert AuditEntry.objects.get(action='approve').outcome == 'ok'


@pytest.mark.django_db
def test_an_undelivered_approval_keeps_the_request_and_revokes(
    operator_client: Client,
    controller_on: None,
    screen_ok: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_server.controller import ca
    from anthias_server.controller import client as client_mod
    from anthias_server.controller.models import RevokedCertificate

    pending = _pending()

    def offline(*_a: Any, **_k: Any) -> Any:
        raise NodeOfflineError('timed out')

    monkeypatch.setattr(client_mod.NodeClient, 'request', offline)
    operator_client.post(
        reverse('controller:screens_approve', args=[pending.pk])
    )
    assert JoinRequest.objects.exists()
    assert not Node.objects.exists()
    revoked = RevokedCertificate.objects.get()
    assert revoked.reason == 'approval not delivered'
    crl = pki.load_crl(ca.crl_pem())
    assert int(revoked.serial, 16) in pki.revoked_serials(crl)
    assert AuditEntry.objects.get(action='approve').outcome == 'failed'


@pytest.mark.django_db
def test_rejecting_tells_the_screen_and_drops_the_request(
    operator_client: Client,
    controller_on: None,
    screen_ok: list[Any],
    screen_calls: list[dict[str, Any]],
) -> None:
    pending = _pending()
    operator_client.post(
        reverse('controller:screens_reject', args=[pending.pk])
    )
    assert [(c['method'], c['path']) for c in screen_calls] == [
        ('DELETE', '/pairing')
    ]
    assert not JoinRequest.objects.exists()
    assert not Node.objects.exists()
    assert AuditEntry.objects.get(action='reject').outcome == 'ok'


@pytest.mark.django_db
def test_approval_needs_an_enabled_controller(
    operator_client: Client,
    controller_on: None,
    screen_ok: list[Any],
    screen_calls: list[dict[str, Any]],
) -> None:
    from anthias_server.settings import settings

    pending = _pending()
    settings['controller_mode'] = False
    settings.save()
    operator_client.post(
        reverse('controller:screens_approve', args=[pending.pk])
    )
    assert screen_calls == []
    assert not Node.objects.exists()


@pytest.mark.django_db
def test_unanswered_requests_expire_in_housekeeping(
    controller_on: None,
    screen_ok: list[Any],
    screen_calls: list[dict[str, Any]],
) -> None:
    pending = _pending()
    JoinRequest.objects.filter(pk=pending.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    tasks._housekeeping()
    assert not JoinRequest.objects.exists()
    assert [c['path'] for c in screen_calls] == ['/pairing']


@pytest.mark.django_db
def test_a_join_request_needs_a_csr_for_the_screen_key(
    controller_on: None, screen_ok: list[Any]
) -> None:
    other = pki.make_csr(pki.new_key(), SCREEN_FP, pki.ROLE_SCREEN)
    for body in (
        _body(join.current_secret(), csr=''),
        _body(join.current_secret(), csr=other),
        _body(join.current_secret(), screen_cert='not a cert'),
        _body(join.current_secret(), screen_fp='a' * 64),
    ):
        response = Client().post(
            JOIN_URL, body, content_type='application/json', headers=M2M
        )
        assert response.status_code == 400, body
    assert not JoinRequest.objects.exists()


@pytest.mark.django_db
def test_pending_requests_are_capped(
    controller_on: None,
    screen_ok: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(join, 'MAX_PENDING', 1)
    JoinRequest.objects.create(
        request_id='r',
        name='Other',
        host='h',
        fingerprint='e' * 64,
        token_ciphertext='',
        csr_pem='',
        expires_at=timezone.now() + join.REQUEST_TTL,
    )
    response = Client().post(
        JOIN_URL,
        _body(join.current_secret()),
        content_type='application/json',
        headers=M2M,
    )
    assert response.status_code == 429


@pytest.mark.django_db
def test_removing_a_screen_revokes_its_certificate(
    operator_client: Client,
    controller_on: None,
    screen_ok: list[Any],
    screen_calls: list[dict[str, Any]],
) -> None:
    from anthias_server.controller import ca

    pending = _pending()
    operator_client.post(
        reverse('controller:screens_approve', args=[pending.pk])
    )
    node = Node.objects.get()
    operator_client.post(reverse('controller:screens_remove', args=[node.pk]))
    assert ca.is_revoked(node.client_serial)
    crl = pki.load_crl(ca.crl_pem())
    assert int(node.client_serial, 16) in pki.revoked_serials(crl)


# --- the CA itself


@pytest.mark.django_db
def test_the_ca_is_created_once_and_its_key_kept_private() -> None:
    from anthias_server.controller import ca

    first, _ = ca.ensure_ca()
    second, _ = ca.ensure_ca()
    assert pki.fingerprint(first) == pki.fingerprint(second)
    pki.check_ca(first)
    assert ca.ca_key_path().stat().st_mode & 0o077 == 0
    assert ca.ca_dir().stat().st_mode & 0o077 == 0


@pytest.mark.django_db
def test_controller_client_certificate_is_reused_then_renewed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_server.controller import ca

    cert_path, _ = ca.ensure_client_cert()
    first = pki.load_cert(Path(cert_path).read_bytes())
    assert pki.role_of(first) == pki.ROLE_CONTROLLER
    ca.ensure_client_cert()
    assert pki.load_cert(Path(cert_path).read_bytes()) == first

    # Close to expiry: reissued, and the old one revoked.
    monkeypatch.setattr(pki, 'needs_renewal', lambda *_a, **_k: True)
    ca.ensure_client_cert()
    second = pki.load_cert(Path(cert_path).read_bytes())
    assert second.serial_number != first.serial_number
    assert ca.is_revoked(pki.serial_hex(first.serial_number))


@pytest.mark.django_db
def test_crl_is_reused_until_the_list_changes_or_it_goes_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_server.controller import ca
    from anthias_server.controller.models import RevokedCertificate

    broadcasts: list[int] = []
    monkeypatch.setattr(ca, '_broadcast_crl', lambda: broadcasts.append(1))

    first = ca.crl_pem()
    assert ca.crl_pem() == first
    assert not ca.refresh_crl()

    assert ca.revoke('abc123', 'Lobby', 'removed')
    assert not ca.revoke('ABC123', 'Lobby', 'removed')
    assert broadcasts == [1]
    listed = pki.load_crl(ca.crl_pem())
    assert pki.revoked_serials(listed) == {0xABC123}

    # A certificate that has expired anyway drops off the list.
    RevokedCertificate.objects.update(
        not_after=timezone.now() - timedelta(days=1)
    )
    assert pki.revoked_serials(pki.load_crl(ca.crl_pem())) == frozenset()

    # Stale: re-signed by housekeeping, and pushed to the screens.
    monkeypatch.setattr(pki, 'CRL_REFRESH_BEFORE', pki.CRL_VALIDITY * 2)
    assert ca.refresh_crl()
    assert broadcasts == [1, 1]


@pytest.mark.django_db
def test_a_signing_request_for_another_key_is_refused() -> None:
    from anthias_server.controller import ca

    csr = pki.make_csr(pki.new_key(), SCREEN_FP, pki.ROLE_SCREEN)
    screen_key = _SCREEN_KEY.public_key()
    with pytest.raises(pki.PkiError):
        ca.sign_screen_csr(csr, screen_key, SCREEN_FP)


def test_the_subject_and_role_come_from_the_ca_not_the_csr() -> None:
    from cryptography.x509.oid import NameOID

    ca_key = pki.new_key()
    authority = pki.make_ca(ca_key, 'CA')
    # The CSR asks for the controller role; the CA decides.
    csr = pki.load_csr(
        pki.make_csr(_SCREEN_KEY, 'whatever', pki.ROLE_CONTROLLER)
    )
    cert = pki.issue_client_cert(
        authority, ca_key, csr.public_key(), SCREEN_FP, pki.ROLE_SCREEN
    )
    assert pki.role_of(cert) == pki.ROLE_SCREEN
    [cn] = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    assert cn.value == SCREEN_FP[:64]
    with pytest.raises(pki.PkiError):
        pki.check_client_cert(cert, authority, pki.ROLE_CONTROLLER)
    expired = cert.not_valid_after_utc + timedelta(seconds=1)
    with pytest.raises(pki.PkiError):
        pki.check_client_cert(cert, authority, pki.ROLE_SCREEN, at=expired)
    other = pki.make_ca(pki.new_key(), 'Other')
    with pytest.raises(pki.PkiError):
        pki.check_client_cert(cert, other, pki.ROLE_SCREEN)
    assert not pki.needs_renewal(cert)
    assert pki.needs_renewal(
        cert, at=cert.not_valid_after_utc - timedelta(days=1)
    )


def test_check_code_is_short_and_readable() -> None:
    assert pki.check_code('abcd1234' + '0' * 56) == 'ABCD-1234'


@pytest.mark.parametrize(
    'pem', ['', 'junk', '-----BEGIN CERTIFICATE-----\nx\n', 'x' * 20000]
)
def test_unreadable_pem_is_a_pki_error(pem: str) -> None:
    for load in (pki.load_cert, pki.load_csr, pki.load_crl):
        with pytest.raises(pki.PkiError):
            load(pem)
