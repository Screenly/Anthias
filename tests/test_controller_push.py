"""Screens pushing status to the controller over a WebSocket.

Controller side: ``anthias_server.controller.push`` (the consumer, and
the polling fallback in ``controller.tasks``). Screen side:
``anthias_server.lib.push``. Caddy side: the m2m site asks for a client
certificate and forwards its fingerprint.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import json
import ssl
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from asgiref.testing import ApplicationCommunicator
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from django.test import override_settings
from django.utils import timezone

from anthias_server.controller import push as controller_push
from anthias_server.controller import service, tasks, vault
from anthias_server.controller.models import Node, NodeStatus
from anthias_server.lib import caddy, management, pki
from anthias_server.lib import push as screen_push

SCREEN_FP = 'b' * 64
PATH = '/api/v2/controller/ws'
INFO: dict[str, Any] = {
    'loadavg': 0.5,
    'free_space': '17.9\xa0GB',
    'display_power': 'On',
    'anthias_version': 'v2026.11.2',
    'device_model': 'Raspberry Pi 4 Model B',
    'uptime': {'days': 0, 'hours': 3.0},
    'memory': {'total': 1906, 'available': 900},
    'under_voltage': {'supported': True, 'active': False},
    'storage': {'status': 'ok'},
}
_IN_MEMORY_LAYER = {
    'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}
}


@pytest.fixture(autouse=True)
def _vault_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
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


_LAST: dict[str, Any] = {}


def _issue(
    role: str = pki.ROLE_SCREEN, other_ca: bool = False
) -> tuple[x509.Certificate, Any]:
    """A client certificate (and its key) from the controller's CA."""
    from anthias_server.controller import ca

    if other_ca:
        issuer_key = pki.new_key()
        issuer = pki.make_ca(issuer_key, 'Someone else')
    else:
        issuer, issuer_key = ca.ensure_ca()
    key = pki.new_key()
    cert = pki.issue_client_cert(
        issuer, issuer_key, key.public_key(), 'screen', role
    )
    return cert, key


def _node(fingerprint: str = SCREEN_FP, **fields: Any) -> Node:
    """A managed screen holding a current certificate from our CA."""
    cert, key = _issue()
    node = Node.objects.create(
        name=fields.pop('name', 'Lobby'),
        host=fields.pop('host', 'lobby.lan'),
        fingerprint=fingerprint,
        token_ciphertext=vault.encrypt('anthias_x'),
        public_key_pem=pki.public_key_pem(key),
        client_serial=pki.serial_hex(cert.serial_number),
        client_fingerprint=pki.fingerprint(cert),
        client_not_after=cert.not_valid_after_utc,
        **fields,
    )
    _LAST.update(cert=cert, key=key)
    return node


def _der(cert: x509.Certificate) -> bytes:
    return base64.b64encode(cert.public_bytes(serialization.Encoding.DER))


def _headers(
    listener: str | None = 'm2m',
    cert: x509.Certificate | None = None,
    raw: bytes | None = None,
) -> list[tuple[bytes, bytes]]:
    """Headers as Caddy forwards them; the last node's cert by default."""
    headers = []
    if listener is not None:
        headers.append((b'x-anthias-listener', listener.encode()))
    if raw is None:
        cert = cert or _LAST.get('cert')
        raw = _der(cert) if cert is not None else b''
    if raw:
        headers.append((b'x-anthias-client-cert', raw))
    return headers


def _communicator(
    headers: list[tuple[bytes, bytes]],
) -> ApplicationCommunicator:
    from anthias_server.django_project.asgi import application

    return ApplicationCommunicator(
        application,
        {
            'type': 'websocket',
            'path': PATH,
            'raw_path': PATH.encode(),
            'query_string': b'',
            'headers': headers,
            'subprotocols': [],
            'client': ('10.0.0.9', 54321),
            'server': ('127.0.0.1', 8080),
            'scheme': 'ws',
        },
    )


async def _connect(communicator: ApplicationCommunicator) -> dict[str, Any]:
    """Handshake; on accept, also take the CRL the consumer sends first."""
    await communicator.send_input({'type': 'websocket.connect'})
    verdict = await communicator.receive_output(timeout=5)
    if verdict['type'] == 'websocket.accept':
        first = await communicator.receive_output(timeout=5)
        assert first['type'] == 'websocket.send'
        assert json.loads(first['text'])['type'] == 'crl'
    return verdict


async def _send(communicator: ApplicationCommunicator, body: Any) -> None:
    await communicator.send_input(
        {'type': 'websocket.receive', 'text': json.dumps(body)}
    )


async def _close(communicator: ApplicationCommunicator) -> None:
    await communicator.send_input(
        {'type': 'websocket.disconnect', 'code': 1000}
    )
    await communicator.wait(timeout=5)


def _run(body: Any) -> None:
    with override_settings(CHANNEL_LAYERS=_IN_MEMORY_LAYER):
        async_to_sync(body)()


# ---------------------------------------------------------------------------
# controller: the consumer


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('case', 'code'),
    [
        ('no listener', controller_push.CLOSE_NOT_M2M),
        ('ui listener', controller_push.CLOSE_NOT_M2M),
        ('no certificate', controller_push.CLOSE_UNKNOWN_SCREEN),
        ('garbage', controller_push.CLOSE_UNKNOWN_SCREEN),
        ('another CA', controller_push.CLOSE_UNKNOWN_SCREEN),
        ('controller role', controller_push.CLOSE_UNKNOWN_SCREEN),
        ('revoked', controller_push.CLOSE_UNKNOWN_SCREEN),
        ('superseded', controller_push.CLOSE_UNKNOWN_SCREEN),
    ],
)
def test_push_is_refused_without_m2m_listener_or_a_current_certificate(
    controller_on: None, case: str, code: int
) -> None:
    from anthias_server.controller import ca

    node = _node()
    headers = {
        'no listener': lambda: _headers(listener=None),
        'ui listener': lambda: _headers(listener='ui'),
        'no certificate': lambda: _headers(raw=b''),
        'garbage': lambda: _headers(raw=b'not-base64-der!'),
        'another CA': lambda: _headers(cert=_issue(other_ca=True)[0]),
        'controller role': lambda: _headers(
            cert=_issue(role=pki.ROLE_CONTROLLER)[0]
        ),
        # Valid, but no longer this screen's current certificate.
        'superseded': lambda: _headers(cert=_issue()[0]),
        'revoked': lambda: _headers(),
    }[case]()
    if case == 'revoked':
        ca.revoke(node.client_serial, node.name, 'test')

    async def body() -> None:
        verdict = await _connect(_communicator(headers))
        assert verdict == {'type': 'websocket.close', 'code': code}

    _run(body)
    assert Node.objects.get().push_connection == ''


@pytest.mark.django_db
def test_push_wants_the_per_install_listener_secret(
    controller_on: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a Caddy run directory, the bare 'm2m' marker is not enough:
    another container on the Docker network could send it and claim a
    forwarded client certificate."""
    _node()
    monkeypatch.setattr(caddy, 'RUN_DIR', tmp_path)
    management.reset_listener_cache()
    try:
        secret = management.listener_value()

        async def refused() -> None:
            verdict = await _connect(_communicator(_headers()))
            assert verdict == {
                'type': 'websocket.close',
                'code': controller_push.CLOSE_NOT_M2M,
            }

        _run(refused)

        async def accepted() -> None:
            verdict = await _connect(_communicator(_headers(listener=secret)))
            assert verdict['type'] == 'websocket.accept'

        _run(accepted)
    finally:
        management.reset_listener_cache()


@pytest.mark.django_db
def test_push_is_refused_when_controller_mode_is_off(
    _isolated_settings_conf: None,
) -> None:
    _node()

    async def body() -> None:
        verdict = await _connect(_communicator(_headers()))
        assert verdict['type'] == 'websocket.close'

    _run(body)


@pytest.mark.django_db
def test_push_is_refused_for_a_screen_that_left(controller_on: None) -> None:
    _node(status=NodeStatus.UNAUTHORIZED)

    async def body() -> None:
        verdict = await _connect(_communicator(_headers()))
        assert verdict['type'] == 'websocket.close'

    _run(body)


@pytest.mark.django_db
def test_push_lifecycle_online_health_heartbeat_offline(
    controller_on: None,
) -> None:
    node = _node(status=NodeStatus.UNKNOWN)
    seen: dict[str, Any] = {}

    async def body() -> None:
        communicator = _communicator(_headers())
        assert (await _connect(communicator))['type'] == 'websocket.accept'
        seen['connected'] = await _refreshed(node)
        await _send(communicator, {'type': 'hello', 'health': INFO})
        await communicator.receive_nothing(timeout=0.3)
        seen['hello'] = await _refreshed(node)
        await _send(communicator, {'type': 'heartbeat'})
        await communicator.receive_nothing(timeout=0.3)
        seen['heartbeat'] = await _refreshed(node)
        await _close(communicator)

    _run(body)

    connected = seen['connected']
    assert connected.status == NodeStatus.ONLINE
    assert connected.push_connection
    assert connected.push_live
    hello = seen['hello']
    assert hello.version == 'v2026.11.2'
    assert hello.device_model == 'Raspberry Pi 4 Model B'
    assert hello.health['free_space'] == '17.9 GB'
    assert hello.health_fresh
    assert seen['heartbeat'].push_seen_at >= hello.push_seen_at

    node.refresh_from_db()
    assert node.status == NodeStatus.OFFLINE
    assert node.push_connection == ''
    assert not node.push_live
    # First in line for the polling fallback.
    assert node.last_checked_at is None


def _due(node: Node) -> None:
    """Make the node's current certificate due for renewal."""
    Node.objects.filter(pk=node.pk).update(
        client_not_after=timezone.now() + timedelta(days=10)
    )


async def _renew_once(
    cert: x509.Certificate, key: Any, fingerprint: str
) -> list[dict[str, Any]]:
    """Connect with ``cert``, ask once for a renewal; the replies."""
    replies: list[dict[str, Any]] = []
    communicator = _communicator(_headers(cert=cert))
    assert (await _connect(communicator))['type'] == 'websocket.accept'
    csr = pki.make_csr(key, fingerprint, pki.ROLE_SCREEN)
    await _send(communicator, {'type': 'renew', 'csr': csr})
    if not await communicator.receive_nothing(timeout=1):
        out = await communicator.receive_output(timeout=5)
        replies.append(json.loads(out['text']))
    await _close(communicator)
    return replies


@pytest.mark.django_db
def test_a_screen_renews_its_certificate_over_the_push_connection(
    controller_on: None,
) -> None:
    """The old certificate stays valid until the new one is used."""
    from anthias_server.controller import ca

    node = _node()
    _due(node)
    old_cert, key = _LAST['cert'], _LAST['key']
    old_serial = node.client_serial
    replies: list[dict[str, Any]] = []
    crls: list[str] = []

    async def body() -> None:
        replies.extend(await _renew_once(old_cert, key, node.fingerprint))

    _run(body)

    assert [r['type'] for r in replies] == ['certificate']
    issued = pki.load_cert(replies[0]['certificate'])
    pki.check_client_cert(issued, ca.ca_cert(), pki.ROLE_SCREEN, key)
    node.refresh_from_db()
    assert node.client_serial == pki.serial_hex(issued.serial_number)
    assert node.client_fingerprint == pki.fingerprint(issued)
    assert node.client_cert_pem == replies[0]['certificate']
    # Not revoked yet: the reply could have been lost on the way.
    assert node.previous_client_serial == old_serial
    assert not ca.is_revoked(old_serial)

    async def reconnect() -> None:
        communicator = _communicator(_headers(cert=issued))
        await communicator.send_input({'type': 'websocket.connect'})
        assert (await communicator.receive_output(timeout=5))[
            'type'
        ] == 'websocket.accept'
        out = await communicator.receive_output(timeout=5)
        crls.append(json.loads(out['text'])['crl'])
        await _close(communicator)

    _run(reconnect)

    # The screen came back with the new one: the old one is revoked,
    # and the CRL it is sent on connect already lists it.
    assert ca.is_revoked(old_serial)
    assert int(old_serial, 16) in pki.revoked_serials(pki.load_crl(crls[0]))
    node.refresh_from_db()
    assert node.previous_client_serial == ''
    assert node.previous_client_fingerprint == ''


@pytest.mark.django_db
def test_a_lost_renewal_reply_is_sent_again_not_reissued(
    controller_on: None,
) -> None:
    from anthias_server.controller.models import RevokedCertificate

    node = _node()
    _due(node)
    old_cert, key = _LAST['cert'], _LAST['key']
    first: list[dict[str, Any]] = []
    again: list[dict[str, Any]] = []

    async def body() -> None:
        first.extend(await _renew_once(old_cert, key, node.fingerprint))
        # The screen never stored it and still has the old one.
        again.extend(await _renew_once(old_cert, key, node.fingerprint))

    _run(body)

    assert first[0]['certificate'] == again[0]['certificate']
    node.refresh_from_db()
    assert node.client_cert_pem == first[0]['certificate']
    assert not RevokedCertificate.objects.exists()


@pytest.mark.django_db
def test_renewal_is_once_per_connection_and_only_when_due(
    controller_on: None,
) -> None:
    from anthias_server.controller.models import RevokedCertificate

    node = _node()
    serial = node.client_serial
    cert, key = _LAST['cert'], _LAST['key']
    replies: list[dict[str, Any]] = []

    async def not_due() -> None:
        replies.extend(await _renew_once(cert, key, node.fingerprint))

    _run(not_due)
    assert replies == []
    node.refresh_from_db()
    assert node.client_serial == serial

    _due(node)
    outputs: list[dict[str, Any]] = []

    async def twice() -> None:
        communicator = _communicator(_headers(cert=cert))
        assert (await _connect(communicator))['type'] == 'websocket.accept'
        for _ in range(3):
            csr = pki.make_csr(key, node.fingerprint, pki.ROLE_SCREEN)
            await _send(communicator, {'type': 'renew', 'csr': csr})
        outputs.append(await communicator.receive_output(timeout=5))
        assert await communicator.receive_nothing(timeout=0.5)
        await _close(communicator)

    _run(twice)

    assert [json.loads(o['text'])['type'] for o in outputs] == ['certificate']
    assert not RevokedCertificate.objects.exists()


@pytest.mark.django_db
def test_a_renewal_for_another_key_is_refused(controller_on: None) -> None:
    node = _node()
    _due(node)
    serial = node.client_serial
    replies: list[dict[str, Any]] = []

    async def body() -> None:
        replies.extend(
            await _renew_once(_LAST['cert'], pki.new_key(), node.fingerprint)
        )

    _run(body)

    assert replies == []
    node.refresh_from_db()
    assert node.client_serial == serial
    assert node.previous_client_serial == ''


@pytest.mark.django_db
def test_a_revocation_reaches_connected_screens_at_once(
    controller_on: None,
) -> None:
    from asgiref.sync import sync_to_async

    from anthias_server.controller import ca

    _node()
    other = _node('c' * 64, name='Hall', host='hall.lan')
    received: list[dict[str, Any]] = []

    async def body() -> None:
        communicator = _communicator(_headers())
        assert (await _connect(communicator))['type'] == 'websocket.accept'
        await sync_to_async(ca.revoke)(other.client_serial, 'Hall', 'test')
        out = await communicator.receive_output(timeout=5)
        received.append(json.loads(out['text']))
        await _close(communicator)

    _run(body)

    assert received[0]['type'] == 'crl'
    crl = pki.load_crl(received[0]['crl'])
    assert int(other.client_serial, 16) in set(pki.revoked_serials(crl))


async def _refreshed(node: Node) -> Node:
    from asgiref.sync import sync_to_async

    return await sync_to_async(Node.objects.get, thread_sensitive=True)(
        pk=node.pk
    )


@pytest.mark.django_db
def test_an_old_connection_closing_does_not_mark_a_reconnected_screen_offline(
    controller_on: None,
) -> None:
    node = _node()

    async def body() -> None:
        old = _communicator(_headers())
        assert (await _connect(old))['type'] == 'websocket.accept'
        new = _communicator(_headers())
        assert (await _connect(new))['type'] == 'websocket.accept'
        await _close(old)
        current = await _refreshed(node)
        assert current.status == NodeStatus.ONLINE
        assert current.push_live
        await _close(new)

    _run(body)


@pytest.mark.django_db
@pytest.mark.parametrize('text', ['not json', '[1, 2]', 'x' * 70_000])
def test_a_malformed_message_closes_the_connection(
    controller_on: None, text: str
) -> None:
    _node()

    async def body() -> None:
        communicator = _communicator(_headers())
        assert (await _connect(communicator))['type'] == 'websocket.accept'
        await communicator.send_input(
            {'type': 'websocket.receive', 'text': text}
        )
        reply = await communicator.receive_output(timeout=5)
        assert reply == {
            'type': 'websocket.close',
            'code': controller_push.CLOSE_BAD_MESSAGE,
        }

    _run(body)


@pytest.mark.django_db
def test_a_removed_screen_is_disconnected_on_its_next_message(
    controller_on: None,
) -> None:
    node = _node()

    async def body() -> None:
        from asgiref.sync import sync_to_async

        communicator = _communicator(_headers())
        assert (await _connect(communicator))['type'] == 'websocket.accept'
        await sync_to_async(node.delete, thread_sensitive=True)()
        await _send(communicator, {'type': 'heartbeat'})
        reply = await communicator.receive_output(timeout=5)
        assert reply['type'] == 'websocket.close'

    _run(body)


# ---------------------------------------------------------------------------
# controller: polling becomes the fallback


@pytest.mark.django_db
def test_status_poll_skips_pushing_screens_and_waits_for_due_ones(
    controller_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = timezone.now()
    _node('1' * 64, name='Pushing', push_connection='abc', push_seen_at=now)
    _node('2' * 64, name='Never checked')
    _node(
        '3' * 64,
        name='Checked recently',
        last_checked_at=now - timedelta(minutes=5),
    )
    _node(
        '4' * 64,
        name='Due',
        last_checked_at=now - timedelta(minutes=20),
    )
    _node(
        '5' * 64,
        name='Push went quiet',
        push_connection='abc',
        push_seen_at=now - timedelta(minutes=10),
    )
    checked: list[str] = []

    def fake_check(node: Node) -> Node:
        checked.append(node.name)
        return node

    monkeypatch.setattr(service, 'check_node', fake_check)

    tasks.refresh_nodes()

    assert sorted(checked) == ['Due', 'Never checked', 'Push went quiet']


def test_push_timing_holds_together() -> None:
    # A live connection survives one missed heartbeat, not two.
    stale = controller_push.PUSH_STALE.total_seconds()
    assert (
        controller_push.HEARTBEAT_S < stale < 3 * controller_push.HEARTBEAT_S
    )
    # Both sides agree on the cadence and the path.
    assert screen_push.HEARTBEAT_S == controller_push.HEARTBEAT_S
    assert screen_push.HEALTH_PUSH_MAX_S == controller_push.HEALTH_PUSH_MAX_S
    assert screen_push.PATH == controller_push.PATH
    # The fallback poll is slower than the regular tick.
    assert tasks.FALLBACK_POLL_S > tasks.STATUS_INTERVAL_S


# ---------------------------------------------------------------------------
# Caddy: the m2m site asks for the client certificate


def test_m2m_site_requests_a_client_certificate_and_forwards_it() -> None:
    server = management.m2m_server()
    (policy,) = server['tls_connection_policies']
    assert policy['client_authentication'] == {'mode': 'request'}
    # Found on the QA rig: with client auth configured Caddy enforces
    # SNI == Host and answers 421, and peers connect by IP (no SNI).
    assert server['strict_sni_host'] is False
    # The first handler on each route is its body cap.
    proxy = server['routes'][0]['handle'][1]
    assert proxy['headers']['request']['set'][caddy.CLIENT_CERT_HEADER] == [
        '{http.request.tls.client.certificate_der_base64}'
    ]


def test_ui_sites_strip_a_client_supplied_certificate_header() -> None:
    config = caddy.build_config()
    for name, server in config['apps']['http']['servers'].items():
        if name == management.M2M_SERVER_NAME:
            continue
        text = json.dumps(server)
        if 'reverse_proxy' in text:
            assert caddy.CLIENT_CERT_HEADER in text, name


# ---------------------------------------------------------------------------
# screen side


def _make_cert(tmp: Path, name: str, days: int = 365) -> tuple[str, str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=days))
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp / f'{name}.crt', tmp / f'{name}.key'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    der = cert.public_bytes(serialization.Encoding.DER)
    return str(cert_path), str(key_path), hashlib.sha256(der).hexdigest()


def test_push_uri() -> None:
    assert (
        screen_push.push_uri('https://192.168.1.5:8443')
        == 'wss://192.168.1.5:8443/api/v2/controller/ws'
    )
    assert (
        screen_push.push_uri('https://[fd00::1]:8443')
        == 'wss://[fd00::1]:8443/api/v2/controller/ws'
    )
    with pytest.raises(ValueError):
        screen_push.push_uri('http://controller.lan:8443')


def test_warning_flags() -> None:
    assert screen_push.warning_flags(INFO) == frozenset()
    low = {**INFO, 'free_space': '200.0\xa0MB'}
    assert screen_push.warning_flags(low) == {'low_disk'}


class _Controller:
    """A TLS WebSocket server standing in for the controller."""

    def __init__(self, cert: str, key: str) -> None:
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(cert, key)
        # Asks for a client certificate. Caddy's "request" mode doesn't
        # verify it at all; ssl can't express that, so each test trusts
        # the screen's self-signed cert instead (see trust()).
        self.context.verify_mode = ssl.CERT_OPTIONAL
        self.messages: list[dict[str, Any]] = []
        self.client_fingerprints: list[str] = []
        # Set to answer renewal requests with a (fake) certificate.
        self.renewed: str | None = None

    def trust(self, cert: str) -> None:
        self.context.load_verify_locations(cafile=cert)

    async def handler(self, ws: Any) -> None:
        sslobj = ws.transport.get_extra_info('ssl_object')
        der = sslobj.getpeercert(binary_form=True) if sslobj else None
        self.client_fingerprints.append(
            hashlib.sha256(der).hexdigest() if der else ''
        )
        async for raw in ws:
            message = json.loads(raw)
            self.messages.append(message)
            if message.get('type') == 'renew' and self.renewed is not None:
                await ws.send(
                    json.dumps({'type': 'certificate', 'certificate': 'NEW'})
                )


def test_screen_pushes_hello_then_heartbeat_with_its_client_certificate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from websockets.asyncio.server import serve

    ctl_cert, ctl_key, ctl_fp = _make_cert(tmp_path, 'controller')
    scr_cert, scr_key, scr_fp = _make_cert(tmp_path, 'screen')
    controller = _Controller(ctl_cert, ctl_key)
    controller.trust(scr_cert)
    monkeypatch.setattr(screen_push, 'HEARTBEAT_S', 0.2)
    learned: list[bytes] = []
    monkeypatch.setattr(screen_push, 'learn_pin', learned.append)
    monkeypatch.setattr(screen_push, 'collect_health', lambda: dict(INFO))
    checks = iter([None])

    async def body() -> None:
        async with serve(
            controller.handler, '127.0.0.1', 0, ssl=controller.context
        ) as server:
            port = server.sockets[0].getsockname()[1]
            target = screen_push.Target(
                controller_id=1,
                uri=f'wss://127.0.0.1:{port}{screen_push.PATH}',
                fingerprint=ctl_fp,
                cert_path=scr_cert,
                key_path=scr_key,
            )
            calls = {'n': 0}

            def still_target() -> screen_push.Target | None:
                # Managed for one heartbeat, then left management.
                calls['n'] += 1
                return target if calls['n'] == 1 else next(checks, None)

            monkeypatch.setattr(screen_push, 'current_target', still_target)
            await asyncio.wait_for(screen_push.run_session(target), 5)
            await asyncio.sleep(0.1)

    asyncio.run(body())

    assert controller.client_fingerprints == [scr_fp]
    # The screen hands the pinned controller certificate on, so a screen
    # approved before key pins existed can learn the controller's key.
    assert [hashlib.sha256(der).hexdigest() for der in learned] == [ctl_fp]
    assert [m['type'] for m in controller.messages] == ['hello', 'heartbeat']
    assert controller.messages[0]['health']['free_space'] == INFO['free_space']


def test_screen_pushes_health_when_a_warning_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from websockets.asyncio.server import serve

    ctl_cert, ctl_key, ctl_fp = _make_cert(tmp_path, 'controller')
    scr_cert, scr_key, _ = _make_cert(tmp_path, 'screen')
    controller = _Controller(ctl_cert, ctl_key)
    controller.trust(scr_cert)
    monkeypatch.setattr(screen_push, 'HEARTBEAT_S', 0.2)
    learned: list[bytes] = []
    monkeypatch.setattr(screen_push, 'learn_pin', learned.append)
    readings = iter([INFO, {**INFO, 'free_space': '100.0\xa0MB'}])
    monkeypatch.setattr(
        screen_push, 'collect_health', lambda: dict(next(readings))
    )

    async def body() -> None:
        async with serve(
            controller.handler, '127.0.0.1', 0, ssl=controller.context
        ) as server:
            port = server.sockets[0].getsockname()[1]
            target = screen_push.Target(
                1,
                f'wss://127.0.0.1:{port}{screen_push.PATH}',
                ctl_fp,
                scr_cert,
                scr_key,
            )
            answers = iter([target, None])
            monkeypatch.setattr(
                screen_push, 'current_target', lambda: next(answers)
            )
            await asyncio.wait_for(screen_push.run_session(target), 5)
            await asyncio.sleep(0.1)

    asyncio.run(body())

    assert [m['type'] for m in controller.messages] == ['hello', 'health']
    assert controller.messages[1]['health']['free_space'] == '100.0\xa0MB'


def test_screen_asks_to_renew_a_certificate_close_to_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from websockets.asyncio.server import serve

    from anthias_server.lib import management

    ctl_cert, ctl_key, ctl_fp = _make_cert(tmp_path, 'controller')
    scr_cert, scr_key, _ = _make_cert(tmp_path, 'screen', days=10)
    controller = _Controller(ctl_cert, ctl_key)
    controller.trust(scr_cert)
    controller.renewed = 'NEW'
    stored: list[str] = []
    monkeypatch.setattr(screen_push, 'HEARTBEAT_S', 0.2)
    learned: list[bytes] = []
    monkeypatch.setattr(screen_push, 'learn_pin', learned.append)
    monkeypatch.setattr(screen_push, 'collect_health', lambda: dict(INFO))

    def store(pem: str) -> bool:
        stored.append(pem)
        return True

    monkeypatch.setattr(management, 'store_client_cert', store)

    async def body() -> None:
        async with serve(
            controller.handler, '127.0.0.1', 0, ssl=controller.context
        ) as server:
            port = server.sockets[0].getsockname()[1]
            target = screen_push.Target(
                1,
                f'wss://127.0.0.1:{port}{screen_push.PATH}',
                ctl_fp,
                scr_cert,
                scr_key,
            )
            answers = iter([target, target, None])
            monkeypatch.setattr(
                screen_push, 'current_target', lambda: next(answers)
            )
            await asyncio.wait_for(screen_push.run_session(target), 5)
            await asyncio.sleep(0.1)

    asyncio.run(body())

    kinds = [m['type'] for m in controller.messages]
    # One request per session, however many heartbeats follow.
    assert kinds.count('renew') == 1
    csr = pki.load_csr(controller.messages[kinds.index('renew')]['csr'])
    assert csr.subject.rfc4514_string().count(pki.ROLE_SCREEN) == 1
    assert stored == ['NEW']


def test_screen_reconnects_when_its_certificate_is_replaced_over_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A certificate renewed over REST mid-session (the old one is now
    revoked) makes the screen reconnect with it, without asking to renew
    on a connection made with the old certificate."""
    import dataclasses

    from websockets.asyncio.server import serve

    ctl_cert, ctl_key, ctl_fp = _make_cert(tmp_path, 'controller')
    scr_cert, scr_key, _ = _make_cert(tmp_path, 'screen', days=10)
    controller = _Controller(ctl_cert, ctl_key)
    controller.trust(scr_cert)
    monkeypatch.setattr(screen_push, 'HEARTBEAT_S', 0.2)
    monkeypatch.setattr(screen_push, 'learn_pin', lambda der: None)
    monkeypatch.setattr(screen_push, 'collect_health', lambda: dict(INFO))

    async def body() -> None:
        async with serve(
            controller.handler, '127.0.0.1', 0, ssl=controller.context
        ) as server:
            port = server.sockets[0].getsockname()[1]
            target = screen_push.Target(
                1,
                f'wss://127.0.0.1:{port}{screen_push.PATH}',
                ctl_fp,
                scr_cert,
                scr_key,
                screen_push.cert_digest(Path(scr_cert)),
            )
            replaced = dataclasses.replace(target, cert_digest='renewed')
            monkeypatch.setattr(
                screen_push, 'current_target', lambda: replaced
            )
            await asyncio.wait_for(screen_push.run_session(target), 5)
            await asyncio.sleep(0.1)

    asyncio.run(body())

    assert [m['type'] for m in controller.messages] == ['hello']


def test_cert_digest_follows_the_file(tmp_path: Path) -> None:
    path = tmp_path / 'client.crt'
    assert screen_push.cert_digest(path) == ''
    path.write_text('one')
    first = screen_push.cert_digest(path)
    path.write_text('two')
    assert screen_push.cert_digest(path) not in ('', first)


def test_screen_stores_a_pushed_revocation_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_server.lib import management

    stored: list[str] = []

    def store(pem: str) -> bool:
        stored.append(pem)
        return True

    monkeypatch.setattr(management, 'store_crl', store)
    assert screen_push.handle_message('{"type": "crl", "crl": "X"}') == 'crl'
    assert screen_push.handle_message('not json') == ''
    assert screen_push.handle_message('[1]') == ''
    assert stored == ['X']


def test_screen_refuses_a_controller_with_another_certificate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from websockets.asyncio.server import serve

    ctl_cert, ctl_key, _ = _make_cert(tmp_path, 'controller')
    scr_cert, scr_key, _ = _make_cert(tmp_path, 'screen')
    controller = _Controller(ctl_cert, ctl_key)
    controller.trust(scr_cert)
    monkeypatch.setattr(screen_push, 'collect_health', lambda: dict(INFO))

    async def body() -> None:
        async with serve(
            controller.handler, '127.0.0.1', 0, ssl=controller.context
        ) as server:
            port = server.sockets[0].getsockname()[1]
            target = screen_push.Target(
                1,
                f'wss://127.0.0.1:{port}{screen_push.PATH}',
                'f' * 64,
                scr_cert,
                scr_key,
            )
            with pytest.raises(screen_push.PinMismatchError):
                await asyncio.wait_for(screen_push.run_session(target), 5)
            await asyncio.sleep(0.1)

    asyncio.run(body())
    # Nothing was sent to the impostor.
    assert controller.messages == []


@pytest.mark.django_db
def test_screen_has_no_push_target_unless_managed(
    _isolated_settings_conf: None,
) -> None:
    from anthias_server.settings import settings

    settings['auth_backend'] = 'auth_basic'
    assert screen_push.current_target() is None


def test_push_thread_is_not_started_without_caddy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(caddy, 'RUN_DIR', tmp_path / 'missing')
    assert screen_push.start_background_push() is None


@pytest.mark.django_db
def test_push_refuses_a_ca_valid_certificate_for_another_key(
    controller_on: None,
) -> None:
    """The key pin on top of the CA: a certificate from our own CA that
    the records point at, but for a key the screen never joined with
    (as a stolen CA key would mint), is refused."""
    from anthias_server.controller import ca

    node = _node()
    issuer, issuer_key = ca.ensure_ca()
    forged = pki.issue_client_cert(
        issuer,
        issuer_key,
        pki.new_key().public_key(),
        node.fingerprint,
        pki.ROLE_SCREEN,
    )
    Node.objects.filter(pk=node.pk).update(
        client_serial=pki.serial_hex(forged.serial_number),
        client_fingerprint=pki.fingerprint(forged),
    )

    assert controller_push._screen_for(forged) is None

    async def body() -> None:
        verdict = await _connect(_communicator(_headers(cert=forged)))
        assert verdict == {
            'type': 'websocket.close',
            'code': controller_push.CLOSE_UNKNOWN_SCREEN,
        }

    _run(body)
    assert Node.objects.get().push_connection == ''


@pytest.mark.django_db
def test_push_accepts_a_renewed_certificate_for_the_joined_key(
    controller_on: None,
) -> None:
    from anthias_server.controller import ca

    node = _node()
    renewed = ca.issue_for_screen(node.public_key_pem, node.fingerprint)
    controller_push.replace_certificate(node, renewed)

    assert pki.fingerprint(renewed) != pki.fingerprint(_LAST['cert'])
    found = controller_push._screen_for(renewed)
    assert found is not None
    assert found.pk == node.pk


def _run_forever_delays(
    monkeypatch: pytest.MonkeyPatch, sessions: list[bool]
) -> list[float]:
    """The sleeps run_forever does between sessions; each entry of
    ``sessions`` says whether that session opened before it dropped."""
    target = screen_push.Target(
        controller_id=1,
        uri='wss://controller.example.com/x',
        fingerprint='a' * 64,
        cert_path='c',
        key_path='k',
    )
    monkeypatch.setattr(screen_push, 'current_target', lambda: target)
    monkeypatch.setattr('random.uniform', lambda a, b: 1.0)
    outcomes = iter(sessions)

    async def session(
        _target: screen_push.Target, on_open: Any = None
    ) -> None:
        if next(outcomes):
            on_open()
        raise ConnectionError('dropped')

    monkeypatch.setattr(screen_push, 'run_session', session)
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) == len(sessions):
            raise asyncio.CancelledError

    monkeypatch.setattr('asyncio.sleep', fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(screen_push.run_forever())
    return delays


def test_reconnect_backoff_resets_after_a_session_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Five failed attempts back off; then a session runs and drops (a
    # controller restart): the next attempt is quick again.
    delays = _run_forever_delays(
        monkeypatch, [False] * 5 + [True, False, False, False]
    )

    assert delays[:5] == [5, 10, 20, 40, 80]
    assert delays[5] == screen_push.RETRY_MIN_S
    # Soon after a session dropped, retries stay frequent, so screens
    # are back within about half a minute of a restarted controller.
    assert max(delays[6:]) <= screen_push.FAST_RETRY_MAX_S


def test_reconnect_backoff_grows_to_the_cap_without_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays = _run_forever_delays(monkeypatch, [False] * 9)

    assert delays[-1] == screen_push.RETRY_MAX_S


def test_fast_retries_end_after_the_window() -> None:
    assert screen_push.next_delay(20, fast_until=100, now=50) == (
        screen_push.FAST_RETRY_MAX_S
    )
    assert screen_push.next_delay(200, fast_until=100, now=150) == (
        screen_push.RETRY_MAX_S
    )
