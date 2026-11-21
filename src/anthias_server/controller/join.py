"""Join information and the join handshake (Proxmox-cluster style).

The controller shows a snippet on its Screens page::

    anthias-join:<base64url JSON>
    {"v": 1, "url": "https://<controller>:8443", "fp": "<sha256>",
     "secret": "<random>", "name": "<controller name>"}

The operator pastes it into a screen's settings. The screen opens a
connection to ``url`` pinned to ``fp`` (the controller's own node
certificate), mints an API token for the controller, and calls
``POST /api/v2/controller/join`` with the secret, the token and its
own fingerprint. Before storing anything the controller calls the
screen back on ``/api/v2/info``, pinned to that fingerprint and using
that token. Each side therefore pins a fingerprint it received over a
channel it already trusts: the screen got the controller's from the
operator, the controller got the screen's over a pinned connection
authenticated by the secret.
"""

from __future__ import annotations

import base64
import datetime
import hmac
import ipaddress
import json
import logging
import re
import secrets
import socket
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from anthias_server.controller import vault
from anthias_server.controller.client import (
    CONNECT_TIMEOUT_S,
    NodeClient,
    NodeError,
    normalize_fingerprint,
)
from anthias_server.controller.models import (
    DEFAULT_NODE_PORT,
    JoinSecret,
    Node,
    NodeStatus,
)
from anthias_server.controller.models import JoinRequest as PendingJoin
from anthias_server.lib import pki
from anthias_server.lib.management import JOIN_PREFIX, M2M_PORT

SNIPPET_PREFIX = JOIN_PREFIX
SNIPPET_VERSION = 1
CONTROLLER_PORT = M2M_PORT
_SECRET_BYTES = 32
_FP_RE = re.compile(r'^[0-9a-f]{64}$')
_CONTAINER_ID_RE = re.compile(r'^[0-9a-f]{12}$')
_HOST_RE = re.compile(r'^[A-Za-z0-9.\-]{1,253}$')
CALLBACK_READ_TIMEOUT_S = 15

# Joins per client IP per window. Generous for an operator joining a
# handful of screens, tight enough to make guessing the secret (256
# bits) pointless even in principle.
JOIN_RATE_LIMIT = 10
JOIN_RATE_WINDOW_S = 60

# Join information stops working an hour after it was generated. The
# secret is reusable within that window so one snippet can join a room
# of screens, but a snippet that leaks later (chat history, a
# screenshot) cannot enroll a rogue node. Opening the Screens page
# after expiry shows fresh join information.
JOIN_SECRET_TTL_S = 60 * 60

logger = logging.getLogger(__name__)


class JoinError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class JoinRequest:
    secret: str
    token: str
    screen_name: str
    screen_fp: str
    screen_port: int
    screen_host: str
    # The screen's node certificate (its fingerprint is screen_fp) and a
    # CSR for the same key: approving the request signs the CSR.
    screen_cert: str = ''
    csr: str = ''


# How long a join request waits for the operator, and how many can.
REQUEST_TTL = timedelta(hours=24)
MAX_PENDING = 50


def controller_fingerprint() -> str:
    """This device's node certificate fingerprint (created if missing).

    Raises ``management.ManagementError`` if there is no certificate.
    """
    from anthias_server.lib import management

    return management.node_fingerprint()


def current_secret() -> str:
    """The join secret, created on first use or after a foreign restore."""
    with transaction.atomic():
        row = JoinSecret.objects.select_for_update().first()
        if row is not None and _expired(row):
            # Expired join information is useless to show; start a new
            # window so the Screens page always has a working snippet.
            row.delete()
            row = None
        if row is None:
            row = JoinSecret.objects.create(
                ciphertext=vault.encrypt(_new_secret())
            )
        try:
            secret = vault.decrypt(row.ciphertext)
        except vault.VaultError:
            # Backups exclude the device's vault key. A restored join
            # secret cannot be used here; replace it so Screens remains
            # usable and can show fresh join information.
            logger.warning('Replacing an unreadable controller join secret')
            secret = _new_secret()
            row.ciphertext = vault.encrypt(secret)
            row.created_at = timezone.now()
            row.save(update_fields=['ciphertext', 'created_at'])
    return secret


def regenerate_secret() -> str:
    """Replace the secret. Old snippets stop working; joined screens
    are not affected (they hold no copy of it)."""
    secret = _new_secret()
    with transaction.atomic():
        JoinSecret.objects.all().delete()
        JoinSecret.objects.create(ciphertext=vault.encrypt(secret))
    return secret


def _new_secret() -> str:
    raw = secrets.token_bytes(_SECRET_BYTES)
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def controller_url(host: str) -> str:
    try:
        is_v6 = ipaddress.ip_address(host).version == 6
    except ValueError:
        is_v6 = False
    host = f'[{host}]' if is_v6 else host
    return f'https://{host}:{CONTROLLER_PORT}'


def build_snippet(host: str, name: str) -> str:
    payload = {
        'v': SNIPPET_VERSION,
        'url': controller_url(host),
        'fp': controller_fingerprint(),
        'secret': current_secret(),
        'name': name,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(',', ':')).encode()
    )
    return SNIPPET_PREFIX + encoded.rstrip(b'=').decode('ascii')


def parse_join_request(data: Any, client_ip: str) -> JoinRequest:
    if not isinstance(data, dict):
        raise JoinError(400, 'The join request must be a JSON object.')

    def text(key: str, limit: int) -> str:
        value = data.get(key)
        if value is None:
            return ''
        if not isinstance(value, str):
            raise JoinError(400, f'"{key}" must be a string.')
        return value.strip()[:limit]

    secret = text('secret', 200)
    token = text('token', 200)
    screen_fp = normalize_fingerprint(text('screen_fp', 100))
    if not secret or not token:
        raise JoinError(
            400, 'The join request is missing the secret or token.'
        )
    if not _FP_RE.match(screen_fp):
        raise JoinError(
            400,
            'The join request has no valid screen certificate fingerprint.',
        )
    port = data.get('screen_port', DEFAULT_NODE_PORT)
    if isinstance(port, bool) or not isinstance(port, int):
        raise JoinError(400, '"screen_port" must be a number.')
    # Screens always serve management on M2M_PORT. Accepting any port
    # would let a secret holder point the callback at arbitrary
    # services next to the controller and read the outcome.
    if port != M2M_PORT:
        raise JoinError(
            400, f'"screen_port" must be {M2M_PORT}, the management port.'
        )
    host = text('screen_host', 253) or client_ip
    if not host or not _valid_host(host):
        raise JoinError(400, 'The join request has no usable screen address.')
    name = text('screen_name', 100)
    if not name or _CONTAINER_ID_RE.match(name):
        # No player name on the screen: its hostname inside the
        # container is a bare container id. Its address is more useful.
        name = host
    screen_cert = text('screen_cert', pki.MAX_PEM_BYTES)
    csr = text('csr', pki.MAX_PEM_BYTES)
    _check_screen_credentials(screen_cert, csr, screen_fp)
    return JoinRequest(
        secret=secret,
        token=token,
        screen_name=name,
        screen_fp=screen_fp,
        screen_port=port,
        screen_host=host,
        screen_cert=screen_cert,
        csr=csr,
    )


def _check_screen_credentials(screen_cert: str, csr: str, fp: str) -> None:
    """The certificate must be the pinned one; the CSR for its key."""
    if not screen_cert or not csr:
        raise JoinError(
            400,
            'The join request has no certificate or signing request. '
            'Update the screen to the same Anthias version as this '
            'controller.',
        )
    try:
        cert = pki.load_cert(screen_cert)
        signing_request = pki.load_csr(csr)
    except pki.PkiError as exc:
        raise JoinError(400, str(exc)) from exc
    if pki.fingerprint(cert) != fp:
        raise JoinError(
            400, 'The certificate does not match the screen fingerprint.'
        )
    if not pki.same_public_key(
        signing_request.public_key(), cert.public_key()
    ):
        raise JoinError(
            400, "The signing request is not for the screen's own key."
        )


def _valid_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return bool(_HOST_RE.match(host))
    return True


# Names that resolve to this controller's own containers on the Docker
# network, or to the controller itself. A callback there is never a
# screen.
_INTERNAL_HOSTS = frozenset(
    {
        'localhost',
        'redis',
        'anthias-server',
        'anthias-caddy',
        'anthias-celery',
        'anthias-viewer',
        'anthias-wifi-connect',
        'webview',
    }
)


def _blocked_address(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return (
        addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
        or addr.is_multicast
        or addr.is_reserved
    )


def _allowed_callback_host(host: str) -> bool:
    """Refuse callback targets that cannot be a screen on the LAN.

    Loopback, link-local (cloud metadata lives there), multicast and
    the Docker service names of this controller's own stack. A DNS
    name is resolved and every address it returns is checked, so a
    name pointing at 127.0.0.1 is refused too. Unresolvable names are
    left to fail in the callback with a generic error.
    """
    lowered = host.lower().rstrip('.')
    if lowered in _INTERNAL_HOSTS or lowered.endswith('.localhost'):
        return False
    try:
        return not _blocked_address(ipaddress.ip_address(lowered))
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(lowered, M2M_PORT, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return True
    for info in infos:
        try:
            addr = ipaddress.ip_address(str(info[4][0]).split('%', 1)[0])
        except ValueError:
            continue
        if _blocked_address(addr):
            return False
    return True


def secret_expires_at(row: JoinSecret) -> datetime.datetime:
    return row.created_at + datetime.timedelta(seconds=JOIN_SECRET_TTL_S)


def _expired(row: JoinSecret) -> bool:
    return timezone.now() >= secret_expires_at(row)


def current_expiry() -> datetime.datetime | None:
    row = JoinSecret.objects.first()
    return secret_expires_at(row) if row is not None else None


def check_secret(candidate: str) -> bool:
    row = JoinSecret.objects.first()
    if row is None or _expired(row):
        return False
    try:
        secret = vault.decrypt(row.ciphertext)
    except vault.VaultError:
        return False
    return hmac.compare_digest(candidate.encode(), secret.encode())


def rate_limit_key(client_ip: str) -> str:
    """The bucket a client counts against: its IPv4 address, or its
    IPv6 /64, since one host can rotate through a whole /64 at will."""
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return client_ip
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f'{addr}/64', strict=False))
    return str(addr)


def rate_limited(client_ip: str) -> bool:
    import redis

    from anthias_common.utils import connect_to_redis

    key = f'controller:join:{rate_limit_key(client_ip)}'
    try:
        r = connect_to_redis()
        count = r.incr(key)
        if count == 1:
            r.expire(key, JOIN_RATE_WINDOW_S)
    except (redis.RedisError, OSError):
        # A Redis outage must not block joining.
        return False
    return isinstance(count, int) and count > JOIN_RATE_LIMIT


def verify_screen(request: JoinRequest) -> dict[str, Any]:
    host = (
        f'[{request.screen_host}]'
        if ':' in request.screen_host
        else request.screen_host
    )
    base_url = f'https://{host}:{request.screen_port}'
    from anthias_server.controller import service

    try:
        with NodeClient(
            base_url,
            request.screen_fp,
            request.token,
            client_cert=service.controller_client_cert(),
        ) as client:
            info = client.json(
                'GET',
                '/info',
                timeout=(CONNECT_TIMEOUT_S, CALLBACK_READ_TIMEOUT_S),
            )
    except NodeError as exc:
        # The low-level reason (refused, timed out, TLS error) stays in
        # the controller's log. Returning it would turn the callback
        # into a port scanner for anyone holding the join secret.
        logger.warning(
            'Join callback to %s:%s failed: %s',
            request.screen_host,
            request.screen_port,
            exc,
        )
        raise JoinError(
            502,
            f'The controller could not reach this screen back at '
            f'{request.screen_host}:{request.screen_port}. Check that the '
            'controller can reach the screen on that address and port; '
            "the controller's log has the details.",
        ) from exc
    return info if isinstance(info, dict) else {}


def request_join(request: JoinRequest) -> tuple[PendingJoin, bool]:
    """Check and store a join request for the operator to approve.

    Returns (request, created). Nothing is trusted yet: the screen is
    only listed under "Waiting for approval". A screen that asks again
    replaces its earlier request (new token, new CSR, new expiry).
    """
    if not check_secret(request.secret):
        raise JoinError(
            403,
            'The join information is wrong, has expired, or was '
            'regenerated on the controller. Copy it again from the '
            "controller's Screens page.",
        )
    # Checked only after the secret, so an anonymous caller cannot make
    # the controller resolve names it picks.
    if not _allowed_callback_host(request.screen_host):
        raise JoinError(
            400,
            'The screen address points at this controller or at an internal '
            'service. Enter the address the controller can reach the screen '
            'on, as an IP address or DNS name.',
        )
    # Expired requests are dropped by the controller housekeeping task
    # (tasks.controller_housekeeping), not here: rejecting one calls
    # the screen back, which an anonymous join must never wait on.
    if (
        PendingJoin.objects.exclude(fingerprint=request.screen_fp)
        .filter(expires_at__gte=timezone.now())
        .count()
        >= MAX_PENDING
    ):
        raise JoinError(
            429,
            'Too many screens are waiting for approval on this '
            'controller. Approve or reject some first.',
        )
    info = verify_screen(request)
    code = pki.check_code(
        request.secret, controller_fingerprint(), request.screen_fp
    )
    now = timezone.now()
    with transaction.atomic():
        pending, created = (
            PendingJoin.objects.select_for_update().get_or_create(
                fingerprint=request.screen_fp,
                defaults={
                    'request_id': pki.random_request_id(),
                    'expires_at': now + REQUEST_TTL,
                    'token_ciphertext': '',
                    'csr_pem': '',
                },
            )
        )
        pending.request_id = pki.random_request_id()
        pending.name = request.screen_name
        pending.host = request.screen_host
        pending.port = request.screen_port
        pending.token_ciphertext = vault.encrypt(request.token)
        pending.csr_pem = request.csr
        pending.check_code = code
        pending.approving_until = None
        pending.version = str(info.get('anthias_version') or '')[:100]
        pending.device_model = str(info.get('device_model') or '')[:200]
        pending.created_at = now
        pending.expires_at = now + REQUEST_TTL
        pending.save()
    return pending, created


def _client_for_request(pending: PendingJoin) -> NodeClient:
    from anthias_server.controller import service

    return NodeClient(
        pending.base_url,
        pending.fingerprint,
        vault.decrypt(pending.token_ciphertext),
        client_cert=service.controller_client_cert(),
    )


# How long an approval may hold its request while it delivers the
# certificate (the delivery itself times out well before this).
APPROVAL_CLAIM = timedelta(minutes=2)


def _claim(pending: PendingJoin) -> PendingJoin:
    """Lock the request for this approval; refuse expired or busy ones.

    ``select_for_update`` serializes approvals on databases that lock
    rows. SQLite ignores it, so the claim is also a conditional update
    of ``approving_until``: of two approvals racing, only one updates
    the row.
    """
    from django.db.models import Q

    now = timezone.now()
    with transaction.atomic():
        row = (
            PendingJoin.objects.select_for_update()
            .filter(pk=pending.pk)
            .first()
        )
        if row is None:
            raise JoinError(
                404, 'That request is gone; it was approved or rejected.'
            )
        if row.expires_at < now:
            raise JoinError(
                410,
                'That request has expired. Ask the screen to join again.',
            )
        claimed = (
            PendingJoin.objects.filter(pk=row.pk)
            .filter(
                Q(approving_until__isnull=True) | Q(approving_until__lt=now)
            )
            .update(approving_until=now + APPROVAL_CLAIM)
        )
        if not claimed:
            raise JoinError(
                409, 'That request is already being approved. Wait a moment.'
            )
        row.approving_until = now + APPROVAL_CLAIM
    return row


def _release(pending: PendingJoin) -> None:
    PendingJoin.objects.filter(pk=pending.pk).update(approving_until=None)


def approve(pending: PendingJoin) -> Node:
    """Sign the screen's CSR, hand it the certificate, then store it.

    The screen is only added once it has accepted the certificate;
    if it cannot be reached, the request stays listed and the
    certificate just issued is revoked.
    """
    pending = _claim(pending)
    try:
        return _approve(pending)
    except BaseException:
        _release(pending)
        raise


def _approve(pending: PendingJoin) -> Node:
    from anthias_server.controller import ca, service

    csr = pki.load_csr(pending.csr_pem)
    try:
        issued = ca.sign_screen_csr(
            pending.csr_pem, csr.public_key(), pending.fingerprint
        )
        ca_certificate = ca.ca_pem()
        crl = ca.crl_pem()
    except pki.PkiError as exc:
        raise JoinError(400, str(exc)) from exc
    except ca.CaError as exc:
        raise JoinError(503, str(exc)) from exc
    serial = pki.serial_hex(issued.serial_number)
    try:
        with _client_for_request(pending) as client:
            client.request(
                'POST',
                '/pairing/certificate',
                json={
                    'certificate': pki.cert_pem(issued),
                    'ca_certificate': ca_certificate,
                    'crl': crl,
                    'controller_name': service.controller_name(),
                },
                timeout=(CONNECT_TIMEOUT_S, CALLBACK_READ_TIMEOUT_S),
                expected=(200, 204),
            )
    except (NodeError, vault.VaultError) as exc:
        ca.revoke(
            serial,
            pending.name,
            'approval not delivered',
            issued.not_valid_after_utc,
        )
        raise JoinError(
            502,
            f'Could not deliver the approval to {pending.name} '
            f'({exc}). It may have withdrawn its request; ask it to '
            'join again.',
        ) from exc
    with transaction.atomic():
        node, created = Node.objects.select_for_update().get_or_create(
            fingerprint=pending.fingerprint,
            defaults={
                'name': pending.name,
                'host': pending.host,
                'port': pending.port,
                'token_ciphertext': pending.token_ciphertext,
            },
        )
        previous = [
            (node.client_serial, node.client_not_after),
            (node.previous_client_serial, node.previous_client_not_after),
        ]
        node.name = pending.name
        node.host = pending.host
        node.port = pending.port
        node.token_ciphertext = pending.token_ciphertext
        node.version = pending.version
        node.device_model = pending.device_model
        node.public_key_pem = pki.public_key_pem(csr.public_key())
        node.client_serial = serial
        node.client_fingerprint = pki.fingerprint(issued)
        node.client_not_after = issued.not_valid_after_utc
        node.client_cert_pem = pki.cert_pem(issued)
        node.previous_client_serial = ''
        node.previous_client_fingerprint = ''
        node.previous_client_not_after = None
        node.status = NodeStatus.ONLINE
        node.status_detail = ''
        node.last_seen_at = timezone.now()
        node.save()
        pending.delete()
    if not created:
        for old_serial, old_not_after in previous:
            if old_serial and old_serial != serial:
                ca.revoke(
                    old_serial,
                    node.name,
                    'replaced by a new join',
                    old_not_after,
                )
    return node


def reject(pending: PendingJoin) -> None:
    """Drop a request and tell the screen (best effort)."""
    try:
        with _client_for_request(pending) as client:
            client.request(
                'DELETE',
                '/pairing',
                timeout=(CONNECT_TIMEOUT_S, CALLBACK_READ_TIMEOUT_S),
                expected=(204, 403, 404),
            )
    except (NodeError, vault.VaultError) as exc:
        logger.info('Could not tell %s it was rejected: %s', pending.name, exc)
    pending.delete()


def expire_requests() -> int:
    """Drop requests nobody approved in time. Returns how many.

    Best effort, from the housekeeping task: each screen is told it
    was turned down, and a request being approved right now is left
    alone.
    """
    from django.db.models import Q

    now = timezone.now()
    expired = list(
        PendingJoin.objects.filter(expires_at__lt=now).filter(
            Q(approving_until__isnull=True) | Q(approving_until__lt=now)
        )
    )
    for pending in expired:
        try:
            reject(pending)
        except Exception:
            logger.exception('Could not expire the request from %s', pending)
            pending.delete()
    return len(expired)
