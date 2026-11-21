"""Screens pushing their status to the controller over a WebSocket.

A managed screen keeps one connection open to
``wss://<controller>:8443/api/v2/controller/ws`` (see
``anthias_server.lib.push`` for the screen side). The screen proves
who it is with the client certificate this controller's CA issued it
(``controller.ca``): Caddy's m2m site verifies it when given and
forwards it (``X-Anthias-Client-Cert``), and it must be a current,
unrevoked screen certificate, the one last issued to that screen. No
token or other secret is involved.

While a screen's connection is live, the status poll skips it
(``tasks.refresh_nodes``). A closed connection marks the screen offline
at once and puts it first in line for the polling fallback, which
tells a screen that left the network from one that only lost its
socket. uvicorn's WebSocket ping/pong closes a connection whose peer
vanished without a goodbye.

Messages are JSON objects with a ``type``:

* ``hello``: ``{"type": "hello", "health": {...}}`` once per connection;
* ``health``: ``{"type": "health", "health": {...}}`` when a warning
  changes, and at least every :data:`HEALTH_PUSH_MAX_S`;
* ``heartbeat``: ``{"type": "heartbeat"}`` every
  :data:`HEARTBEAT_S`;
* ``renew``: ``{"type": "renew", "csr": "<PEM>"}`` when the screen's
  certificate is close to expiry, answered with
  ``{"type": "certificate", "certificate": "<PEM>"}`` (the old one is
  revoked).

The controller sends ``{"type": "crl", "crl": "<PEM>"}`` on connect
and whenever the revocation list changes.

``health`` carries the screen's ``/api/v2/info`` health fields
(``InfoViewV2.health_payload``), so pushed and polled readings are
the same shape.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import Any

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.layers import get_channel_layer
from cryptography import x509
from django.db.models import Q
from django.utils import timezone

from anthias_server.lib import pki

from .models import Node, NodeStatus

logger = logging.getLogger(__name__)

PATH = '/api/v2/controller/ws'
HEARTBEAT_S = 60
HEALTH_PUSH_MAX_S = 15 * 60
# Two missed heartbeats: the connection is no longer counted as live,
# so the polling fallback takes over even if the close was never seen.
PUSH_STALE = timedelta(seconds=2.5 * HEARTBEAT_S)
MAX_MESSAGE_BYTES = 64 * 1024

# Close codes (4000-4999 are application-defined).
CLOSE_NOT_M2M = 4404
CLOSE_UNKNOWN_SCREEN = 4403
CLOSE_BAD_MESSAGE = 4400

# Every connected screen's consumer joins this Channels group, so a new
# CRL reaches all of them at once (broadcast_crl).
GROUP = 'controller-screens'


def is_live(node: Node, now: datetime) -> bool:
    return (
        bool(node.push_connection)
        and node.push_seen_at is not None
        and now - node.push_seen_at <= PUSH_STALE
    )


def live_filter(now: datetime) -> Q:
    """ORM filter matching nodes whose push connection is live."""
    return ~Q(push_connection='') & Q(push_seen_at__gte=now - PUSH_STALE)


def _header(scope: dict[str, Any], name: str) -> str:
    wanted = name.lower().encode()
    for key, value in scope.get('headers', []):
        if key.lower() == wanted:
            return str(value.decode('latin-1'))
    return ''


def client_certificate(scope: dict[str, Any]) -> x509.Certificate | None:
    """The TLS client certificate Caddy forwarded, or None."""
    from anthias_server.lib import caddy

    raw = _header(scope, caddy.CLIENT_CERT_HEADER)
    if not raw:
        return None
    try:
        return x509.load_der_x509_certificate(base64.b64decode(raw))
    except (ValueError, binascii.Error):
        return None


def _screen_for(cert: x509.Certificate | None) -> Node | None:
    """The managed screen this client certificate currently belongs to.

    It must be a screen certificate from this controller's CA, valid
    now, not revoked, and the one most recently issued to that screen.
    """
    from . import ca

    if cert is None:
        return None
    try:
        pki.check_client_cert(cert, ca.ca_cert(), pki.ROLE_SCREEN)
    except pki.PkiError:
        return None
    if ca.is_revoked(pki.serial_hex(cert.serial_number)):
        return None
    return (
        Node.objects.exclude(status=NodeStatus.UNAUTHORIZED)
        .filter(client_fingerprint=pki.fingerprint(cert))
        .first()
    )


def _attach(cert: x509.Certificate | None, connection: str) -> int | None:
    """Mark the screen presenting ``cert`` as connected; its pk."""
    from . import service

    if not service.controller_enabled():
        return None
    node = _screen_for(cert)
    if node is None:
        return None
    node.push_connection = connection
    node.push_seen_at = timezone.now()
    node.save(update_fields=['push_connection', 'push_seen_at'])
    service._set_status(node, NodeStatus.ONLINE)
    return node.pk


def _record(node_id: int, connection: str, message: dict[str, Any]) -> bool:
    """Apply one message. False when this connection was replaced."""
    from . import service
    from .health import health_from_info

    node = Node.objects.filter(pk=node_id, push_connection=connection).first()
    if node is None:
        return False
    now = timezone.now()
    node.push_seen_at = now
    fields = ['push_seen_at']
    health = message.get('health')
    if message.get('type') in ('hello', 'health') and isinstance(health, dict):
        node.health = health_from_info(health)
        node.health_at = now
        node.version = str(health.get('anthias_version') or '')[:100]
        node.device_model = str(health.get('device_model') or '')[:200]
        fields += ['health', 'health_at', 'version', 'device_model']
    node.save(update_fields=fields)
    if node.status != NodeStatus.ONLINE:
        service._set_status(node, NodeStatus.ONLINE)
    else:
        Node.objects.filter(pk=node.pk).update(last_seen_at=now)
    return True


def _detach(node_id: int, connection: str) -> None:
    """The connection closed: offline, and first in line for a poll.

    A screen that reconnected already replaced ``push_connection``, so
    the old connection's close changes nothing.
    """
    from . import service

    updated = Node.objects.filter(
        pk=node_id, push_connection=connection
    ).update(push_connection='')
    if not updated:
        return
    node = Node.objects.get(pk=node_id)
    if node.status == NodeStatus.ONLINE:
        service._set_status(
            node,
            NodeStatus.OFFLINE,
            'Its connection to this controller closed.',
        )
    # After _set_status, which stamps last_checked_at: cleared, the
    # next status run polls this screen first.
    Node.objects.filter(pk=node_id).update(last_checked_at=None)


def _renew(node_id: int, connection: str, csr_pem: str) -> dict[str, Any]:
    """Sign a renewal CSR; revoke the certificate it replaces."""
    from . import ca

    node = Node.objects.filter(pk=node_id, push_connection=connection).first()
    if node is None or not node.public_key_pem:
        raise pki.PkiError('This screen cannot renew its certificate.')
    issued = ca.sign_screen_csr(
        csr_pem, pki.load_public_key(node.public_key_pem), node.fingerprint
    )
    previous = (node.client_serial, node.client_not_after)
    node.client_serial = pki.serial_hex(issued.serial_number)
    node.client_fingerprint = pki.fingerprint(issued)
    node.client_not_after = issued.not_valid_after_utc
    node.save(
        update_fields=[
            'client_serial',
            'client_fingerprint',
            'client_not_after',
        ]
    )
    if previous[0]:
        ca.revoke(previous[0], node.name, 'renewed', previous[1])
    logger.info('Renewed the client certificate of screen %s', node_id)
    return {'type': 'certificate', 'certificate': pki.cert_pem(issued)}


def _crl_message() -> dict[str, Any]:
    from . import ca

    return {'type': 'crl', 'crl': ca.crl_pem()}


def broadcast_crl() -> None:
    """Send the current CRL to every connected screen."""
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(GROUP, {'type': 'crl.update'})


def _parse(text_data: str | None) -> dict[str, Any] | None:
    """A pushed message as a dict, or None when it isn't one."""
    if text_data is None or len(text_data) > MAX_MESSAGE_BYTES:
        return None
    try:
        message = json.loads(text_data)
    except ValueError:
        return None
    return message if isinstance(message, dict) else None


class ScreenPushConsumer(AsyncWebsocketConsumer):
    node_id: int | None = None
    connection: str = ''

    async def connect(self) -> None:
        from anthias_server.lib import management

        listener = _header(self.scope, 'X-Anthias-Listener')
        if not hmac.compare_digest(listener, management.M2M_LISTENER_VALUE):
            await self.close(code=CLOSE_NOT_M2M)
            return
        cert = client_certificate(self.scope)
        if cert is None:
            await self.close(code=CLOSE_UNKNOWN_SCREEN)
            return
        self.connection = secrets.token_hex(8)
        self.node_id = await database_sync_to_async(_attach)(
            cert, self.connection
        )
        if self.node_id is None:
            logger.info('Push connection refused: unknown screen')
            await self.close(code=CLOSE_UNKNOWN_SCREEN)
            return
        await self.accept()
        if self.channel_layer is not None:
            await self.channel_layer.group_add(GROUP, self.channel_name)
        await self._send_json(await database_sync_to_async(_crl_message)())
        logger.info('Screen %s connected for push', self.node_id)

    async def _send_json(self, message: dict[str, Any]) -> None:
        await self.send(text_data=json.dumps(message))

    async def crl_update(self, _event: dict[str, Any]) -> None:
        """Channels group message: the CRL changed."""
        await self._send_json(await database_sync_to_async(_crl_message)())

    async def receive(
        self, text_data: str | None = None, bytes_data: bytes | None = None
    ) -> None:
        if self.node_id is None:
            return
        message = _parse(text_data)
        if message is None:
            logger.info('Bad push message from screen %s', self.node_id)
            await self.close(code=CLOSE_BAD_MESSAGE)
            return
        if message.get('type') == 'renew':
            try:
                reply = await database_sync_to_async(_renew)(
                    self.node_id, self.connection, str(message.get('csr', ''))
                )
            except pki.PkiError as exc:
                logger.warning(
                    'Renewal refused for screen %s: %s', self.node_id, exc
                )
                await self.close(code=CLOSE_BAD_MESSAGE)
                return
            await self._send_json(reply)
            return
        current = await database_sync_to_async(_record)(
            self.node_id, self.connection, message
        )
        if not current:
            await self.close()

    async def disconnect(self, code: int) -> None:
        if self.node_id is None:
            return
        if self.channel_layer is not None:
            await self.channel_layer.group_discard(GROUP, self.channel_name)
        await database_sync_to_async(_detach)(self.node_id, self.connection)
        logger.info(
            'Screen %s push connection closed (%s)', self.node_id, code
        )
