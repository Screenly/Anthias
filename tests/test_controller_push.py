"""Screens pushing status to the controller over a WebSocket.

Controller side: ``anthias_server.controller.push`` (the consumer, and
the polling fallback in ``controller.tasks``). Screen side:
``anthias_server.lib.push``. Caddy side: the m2m site asks for a client
certificate and forwards its fingerprint.
"""

from __future__ import annotations

import asyncio
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
from anthias_server.lib import caddy, management
from anthias_server.lib import push as screen_push

SCREEN_FP = 'b' * 64
OTHER_FP = 'e' * 64
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


def _node(fingerprint: str = SCREEN_FP, **fields: Any) -> Node:
    return Node.objects.create(
        name=fields.pop('name', 'Lobby'),
        host=fields.pop('host', 'lobby.lan'),
        fingerprint=fingerprint,
        token_ciphertext=vault.encrypt('anthias_x'),
        **fields,
    )


def _headers(
    listener: str | None = 'm2m', fingerprint: str | None = SCREEN_FP
) -> list[tuple[bytes, bytes]]:
    headers = []
    if listener is not None:
        headers.append((b'x-anthias-listener', listener.encode()))
    if fingerprint is not None:
        headers.append((b'x-anthias-client-fingerprint', fingerprint.encode()))
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
    await communicator.send_input({'type': 'websocket.connect'})
    return await communicator.receive_output(timeout=5)


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
    ('headers', 'code'),
    [
        (_headers(listener=None), controller_push.CLOSE_NOT_M2M),
        (_headers(listener='ui'), controller_push.CLOSE_NOT_M2M),
        (_headers(fingerprint=None), controller_push.CLOSE_UNKNOWN_SCREEN),
        (_headers(fingerprint=''), controller_push.CLOSE_UNKNOWN_SCREEN),
        (
            _headers(fingerprint=OTHER_FP),
            controller_push.CLOSE_UNKNOWN_SCREEN,
        ),
    ],
)
def test_push_is_refused_without_m2m_listener_or_a_known_screen(
    controller_on: None, headers: list[tuple[bytes, bytes]], code: int
) -> None:
    _node()

    async def body() -> None:
        verdict = await _connect(_communicator(headers))
        assert verdict == {'type': 'websocket.close', 'code': code}

    _run(body)
    assert Node.objects.get().push_connection == ''


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
    proxy = server['routes'][0]['handle'][0]
    assert proxy['headers']['request']['set'][
        caddy.CLIENT_FINGERPRINT_HEADER
    ] == ['{http.request.tls.client.fingerprint}']


def test_ui_sites_strip_a_client_supplied_fingerprint_header() -> None:
    config = caddy.build_config()
    for name, server in config['apps']['http']['servers'].items():
        if name == management.M2M_SERVER_NAME:
            continue
        text = json.dumps(server)
        if 'reverse_proxy' in text:
            assert caddy.CLIENT_FINGERPRINT_HEADER in text, name


# ---------------------------------------------------------------------------
# screen side


def _make_cert(tmp: Path, name: str) -> tuple[str, str, str]:
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
        .not_valid_after(now + dt.timedelta(days=30))
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

    def trust(self, cert: str) -> None:
        self.context.load_verify_locations(cafile=cert)

    async def handler(self, ws: Any) -> None:
        sslobj = ws.transport.get_extra_info('ssl_object')
        der = sslobj.getpeercert(binary_form=True) if sslobj else None
        self.client_fingerprints.append(
            hashlib.sha256(der).hexdigest() if der else ''
        )
        async for raw in ws:
            self.messages.append(json.loads(raw))


def test_screen_pushes_hello_then_heartbeat_with_its_client_certificate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from websockets.asyncio.server import serve

    ctl_cert, ctl_key, ctl_fp = _make_cert(tmp_path, 'controller')
    scr_cert, scr_key, scr_fp = _make_cert(tmp_path, 'screen')
    controller = _Controller(ctl_cert, ctl_key)
    controller.trust(scr_cert)
    monkeypatch.setattr(screen_push, 'HEARTBEAT_S', 0.2)
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
