"""The controller CA: failing closed, creation under a lock, the CRL
counter and pruning, and renewing screen certificates."""

from __future__ import annotations

import errno
import multiprocessing
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from anthias_server.controller import ca, service, tasks, vault
from anthias_server.controller.client import NodeOfflineError
from anthias_server.controller.models import (
    CrlCounter,
    Node,
    RevokedCertificate,
)
from anthias_server.lib import management, pki

CONTROLLER_FP = 'c' * 64
SCREEN_FP = 'b' * 64


@pytest.fixture(autouse=True)
def _identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    monkeypatch.setattr(management, 'node_fingerprint', lambda: CONTROLLER_FP)
    monkeypatch.setattr(management, 'sync_state', lambda: None)
    monkeypatch.setattr(ca, '_broadcast_crl', lambda: None)
    monkeypatch.setattr(vault, 'key_path', lambda: str(tmp_path / 'k.key'))
    vault.reset_cache()
    yield
    vault.reset_cache()


@pytest.fixture
def controller_on(_isolated_settings_conf: None) -> Iterator[None]:
    from anthias_server.settings import settings

    settings['auth_backend'] = 'auth_basic'
    settings['controller_mode'] = True
    settings.save()
    yield


# --- failing closed -----------------------------------------------------


def test_an_unparsable_key_is_never_replaced() -> None:
    cert, _ = ca.ensure_ca()
    ca.ca_key_path().write_bytes(b'garbage')
    with pytest.raises(ca.CaUnavailableError, match='ca.key'):
        ca.ensure_ca()
    assert ca.ca_key_path().read_bytes() == b'garbage'
    assert pki.load_cert(ca.ca_cert_path().read_bytes()) == cert
    assert 'ca.key' in ca.problem()


def test_a_read_error_is_not_a_missing_ca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An SD-card EIO must not turn into a new CA."""
    ca.ensure_ca()
    before = ca.ca_key_path().read_bytes()
    real_read = Path.read_bytes

    def flaky(self: Path) -> bytes:
        if self.name == 'ca.key':
            raise OSError(errno.EIO, 'Input/output error')
        return real_read(self)

    monkeypatch.setattr(Path, 'read_bytes', flaky)
    with pytest.raises(ca.CaUnavailableError, match='Input/output'):
        ca.ensure_ca()
    monkeypatch.setattr(Path, 'read_bytes', real_read)
    assert ca.ca_key_path().read_bytes() == before


def test_a_certificate_without_its_key_is_unusable() -> None:
    ca.ensure_ca()
    ca.ca_key_path().unlink()
    with pytest.raises(ca.CaUnavailableError, match='missing'):
        ca.ensure_ca()
    assert not ca.ca_key_path().exists()


def test_a_certificate_for_another_key_is_unusable() -> None:
    ca.ensure_ca()
    other = pki.make_ca(pki.new_key(), 'Other')
    ca.ca_cert_path().write_text(pki.cert_pem(other))
    with pytest.raises(ca.CaUnavailableError, match='does not belong'):
        ca.ensure_ca()


def test_a_key_without_its_certificate_gets_it_back() -> None:
    """A crash between the two writes at creation, before any screen
    could have seen the certificate."""
    _, key = ca.ensure_ca()
    ca.ca_cert_path().unlink()
    cert, again = ca.ensure_ca()
    assert pki.key_pem(again) == pki.key_pem(key)
    assert pki.same_public_key(cert.public_key(), key)


def test_ca_files_are_created_but_never_overwritten(tmp_path: Path) -> None:
    target = tmp_path / 'x'
    ca._create(target, b'one', 0o600)
    with pytest.raises(FileExistsError):
        ca._create(target, b'two', 0o600)
    assert target.read_bytes() == b'one'
    assert [p.name for p in tmp_path.iterdir()] == ['x']


def _create_in_child(queue: Any) -> None:
    cert, _ = ca.ensure_ca()
    queue.put(pki.fingerprint(cert))


def test_processes_racing_to_create_it_get_the_same_ca() -> None:
    context = multiprocessing.get_context('fork')
    queue = context.Queue()
    workers = [
        context.Process(target=_create_in_child, args=(queue,))
        for _ in range(4)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(30)
        assert worker.exitcode == 0
    fingerprints = {queue.get(timeout=5) for _ in workers}
    assert fingerprints == {ca.ca_fingerprint()}


def _write_in_child(target: Path, data: bytes) -> None:
    for _ in range(200):
        ca._write(target, data, 0o644)


def test_processes_writing_the_same_file_do_not_collide(
    tmp_path: Path,
) -> None:
    """Web, Celery and the push consumer all re-sign the CRL; their
    temporary files must not share a name, or one renames the other's
    away (FileNotFoundError, seen on a controller)."""
    context = multiprocessing.get_context('fork')
    target = tmp_path / 'crl.pem'
    workers = [
        context.Process(
            target=_write_in_child, args=(target, f'{n}'.encode() * 64)
        )
        for n in range(4)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(60)
        assert worker.exitcode == 0
    assert len(set(target.read_bytes())) == 1
    assert [p.name for p in tmp_path.iterdir()] == ['crl.pem']


@pytest.mark.django_db
def test_the_screens_page_shows_an_unusable_ca(controller_on: None) -> None:
    ca.ensure_ca()
    ca.ca_key_path().write_bytes(b'garbage')
    client = Client()
    client.force_login(User.objects.create_superuser('op', password='x'))
    html = client.get(reverse('controller:screens')).content.decode()
    assert 'id="ca-problem"' in html
    assert 'certificate authority is unusable' in html
    assert ca.ca_key_path().read_bytes() == b'garbage'


@pytest.mark.django_db
def test_calls_to_screens_stop_while_the_ca_is_unusable() -> None:
    ca.ensure_ca()
    ca.ca_key_path().write_bytes(b'garbage')
    with pytest.raises(NodeOfflineError, match='controller CA'):
        service.controller_client_cert()


# --- the CRL ------------------------------------------------------------


@pytest.mark.django_db
def test_the_crl_number_comes_from_a_counter_not_the_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    CrlCounter.objects.update_or_create(pk=1, defaults={'number': 41})
    first = pki.crl_number(pki.load_crl(ca.crl_pem()))
    assert first == 42
    ca.revoke('abc', 'x', 'test')
    second = pki.crl_number(pki.load_crl(ca.crl_pem()))
    assert second == 43
    assert CrlCounter.objects.get().number == 43


@pytest.mark.django_db
def test_the_crl_number_continues_past_a_lost_counter() -> None:
    CrlCounter.objects.update_or_create(pk=1, defaults={'number': 1000})
    ca.crl_pem()
    # The database came back from an older backup.
    CrlCounter.objects.update(number=5)
    ca.revoke('abc', 'x', 'test')
    assert pki.crl_number(pki.load_crl(ca.crl_pem())) == 1002


@pytest.mark.django_db
def test_revocations_nothing_needs_are_pruned() -> None:
    now = timezone.now()
    RevokedCertificate.objects.create(
        serial='a1', not_after=now - timedelta(days=1)
    )
    RevokedCertificate.objects.create(
        serial='a2', not_after=now + timedelta(days=1)
    )
    old = RevokedCertificate.objects.create(serial='a3', not_after=None)
    RevokedCertificate.objects.filter(pk=old.pk).update(
        revoked_at=now - pki.CLIENT_VALIDITY - timedelta(days=1)
    )
    RevokedCertificate.objects.create(serial='a4', not_after=None)
    assert ca.prune_revocations() == 2
    assert set(
        RevokedCertificate.objects.values_list('serial', flat=True)
    ) == {'a2', 'a4'}
    listed = pki.revoked_serials(pki.load_crl(ca.crl_pem()))
    assert listed == {0xA2, 0xA4}


@pytest.mark.django_db
def test_a_crl_of_many_revocations_still_loads() -> None:
    """Hundreds of revocations stay within what screens accept."""
    later = timezone.now() + timedelta(days=30)
    RevokedCertificate.objects.bulk_create(
        RevokedCertificate(serial=format(10**30 + i, 'x'), not_after=later)
        for i in range(500)
    )
    text = ca.crl_pem()
    assert len(text) > pki.MAX_PEM_BYTES
    assert len(pki.revoked_serials(pki.load_crl(text))) == 500


# --- controller-driven renewal over REST --------------------------------


def _node(**fields: Any) -> tuple[Node, Any]:
    authority, authority_key = ca.ensure_ca()
    key = pki.new_key()
    cert = pki.issue_client_cert(
        authority, authority_key, key.public_key(), SCREEN_FP, pki.ROLE_SCREEN
    )
    node = Node.objects.create(
        name='Lobby',
        host='lobby.lan',
        fingerprint=SCREEN_FP,
        token_ciphertext=vault.encrypt('anthias_x'),
        public_key_pem=pki.public_key_pem(key),
        client_serial=pki.serial_hex(cert.serial_number),
        client_fingerprint=pki.fingerprint(cert),
        client_not_after=cert.not_valid_after_utc,
        client_cert_pem=pki.cert_pem(cert),
        **fields,
    )
    return node, key


@pytest.fixture
def screen_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    from anthias_server.controller import client as client_mod

    calls: list[dict[str, Any]] = []

    def fake_request(self: Any, method: str, path: str, **kw: Any) -> Any:
        calls.append({'method': method, 'path': path, 'json': kw.get('json')})

    monkeypatch.setattr(client_mod.NodeClient, 'request', fake_request)
    return calls


@pytest.mark.django_db
def test_the_controller_renews_a_certificate_over_rest(
    controller_on: None, screen_calls: list[dict[str, Any]]
) -> None:
    node, key = _node()
    old_serial = node.client_serial
    assert service.renew_certificate(node) == 'renewed'
    [call] = screen_calls
    assert (call['method'], call['path']) == ('POST', '/pairing/certificate')
    issued = pki.load_cert(call['json']['certificate'])
    # For the key bound at join, from our CA, screen role.
    pki.check_client_cert(issued, ca.ca_cert(), pki.ROLE_SCREEN, key)
    assert call['json']['ca_certificate'] == ca.ca_pem()
    node.refresh_from_db()
    assert node.client_serial == pki.serial_hex(issued.serial_number)
    assert node.client_cert_pem == call['json']['certificate']
    # The screen confirmed it: the old one is revoked at once.
    assert ca.is_revoked(old_serial)
    assert node.previous_client_serial == ''


@pytest.mark.django_db
def test_a_failed_rest_renewal_changes_nothing(
    controller_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from anthias_server.controller import client as client_mod

    node, _ = _node()
    before = (node.client_serial, node.client_fingerprint)

    def unreachable(self: Any, *_a: Any, **_k: Any) -> Any:
        raise NodeOfflineError('refused')

    monkeypatch.setattr(client_mod.NodeClient, 'request', unreachable)
    assert service.renew_certificate(node).startswith('not renewed')
    node.refresh_from_db()
    assert (node.client_serial, node.client_fingerprint) == before
    assert not RevokedCertificate.objects.exists()


@pytest.mark.django_db
def test_housekeeping_renews_only_screens_that_need_it(
    controller_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = timezone.now()
    queued: list[int] = []
    monkeypatch.setattr(
        tasks.renew_node_certificate, 'delay', lambda pk: queued.append(pk)
    )
    fresh, _ = _node()
    Node.objects.filter(pk=fresh.pk).update(fingerprint='1' * 64)
    due_offline, _ = _node()
    Node.objects.filter(pk=due_offline.pk).update(
        fingerprint='2' * 64, client_not_after=now + timedelta(days=20)
    )
    due_live, _ = _node()
    Node.objects.filter(pk=due_live.pk).update(
        fingerprint='3' * 64,
        client_not_after=now + timedelta(days=20),
        push_connection='abc',
        push_seen_at=now,
    )
    overdue_live, _ = _node()
    Node.objects.filter(pk=overdue_live.pk).update(
        fingerprint='4' * 64,
        client_not_after=now + timedelta(days=5),
        push_connection='def',
        push_seen_at=now,
    )
    tasks.controller_housekeeping()
    # A live screen renews over its connection; the controller steps in
    # for one without a connection, or one that is running late.
    assert sorted(queued) == sorted([due_offline.pk, overdue_live.pk])


@pytest.mark.django_db
def test_housekeeping_prunes_and_resigns_the_crl(controller_on: None) -> None:
    RevokedCertificate.objects.create(
        serial='a1', not_after=timezone.now() - timedelta(days=1)
    )
    tasks.controller_housekeeping()
    assert not RevokedCertificate.objects.exists()
    assert ca.crl_path().exists()
