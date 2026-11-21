"""Controller mode: join information, the join endpoint, the Screens page."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from anthias_server.controller import join, service, tasks, vault
from anthias_server.controller.client import NodeOfflineError
from anthias_server.controller.models import (
    AuditEntry,
    JoinSecret,
    Node,
    NodeStatus,
    PublishState,
)
from anthias_server.lib import management

_PWD = 'fixture-controller-operator-pwd'  # NOSONAR
CONTROLLER_FP = 'c' * 64
SCREEN_FP = 'b' * 64
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
    assert response.status_code == 201, response.content
    node = Node.objects.get()
    assert response.json() == {
        'controller_name': 'Head office',
        'screen_id': node.pk,
    }
    assert (node.name, node.host, node.port) == ('Lobby', '10.0.0.7', 8443)
    assert node.fingerprint == SCREEN_FP
    assert node.status == NodeStatus.ONLINE
    assert node.version == 'v2026.11.2'
    assert 'anthias_screen_token' not in node.token_ciphertext
    assert vault.decrypt(node.token_ciphertext) == 'anthias_screen_token'
    assert screen_ok[0].screen_fp == SCREEN_FP
    assert AuditEntry.objects.get(action='join').outcome == 'ok'


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
    assert Node.objects.get().host == '192.168.1.50'


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
    node = Node.objects.get()
    assert node.host == '10.0.0.8'
    assert vault.decrypt(node.token_ciphertext) == 'anthias_two'


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
