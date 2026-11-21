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
import hmac
import ipaddress
import json
import re
import secrets
from dataclasses import dataclass
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


def controller_fingerprint() -> str:
    """This device's node certificate fingerprint (created if missing).

    Raises ``management.ManagementError`` if there is no certificate.
    """
    from anthias_server.lib import management

    return management.node_fingerprint()


def current_secret() -> str:
    """The join secret, created on first use."""
    with transaction.atomic():
        row = JoinSecret.objects.select_for_update().first()
        if row is None:
            row = JoinSecret.objects.create(
                ciphertext=vault.encrypt(_new_secret())
            )
    return vault.decrypt(row.ciphertext)


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
    if not 0 < port < 65536:
        raise JoinError(400, '"screen_port" is out of range.')
    host = text('screen_host', 253) or client_ip
    if not host or not _valid_host(host):
        raise JoinError(400, 'The join request has no usable screen address.')
    name = text('screen_name', 100)
    if not name or _CONTAINER_ID_RE.match(name):
        # No player name on the screen: its hostname inside the
        # container is a bare container id. Its address is more useful.
        name = host
    return JoinRequest(
        secret=secret,
        token=token,
        screen_name=name,
        screen_fp=screen_fp,
        screen_port=port,
        screen_host=host,
    )


def _valid_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return bool(_HOST_RE.match(host))
    return True


def check_secret(candidate: str) -> bool:
    row = JoinSecret.objects.first()
    if row is None:
        return False
    try:
        secret = vault.decrypt(row.ciphertext)
    except vault.VaultError:
        return False
    return hmac.compare_digest(candidate.encode(), secret.encode())


def rate_limited(client_ip: str) -> bool:
    import redis

    from anthias_common.utils import connect_to_redis

    key = f'controller:join:{client_ip}'
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
    try:
        with NodeClient(base_url, request.screen_fp, request.token) as client:
            info = client.json(
                'GET',
                '/info',
                timeout=(CONNECT_TIMEOUT_S, CALLBACK_READ_TIMEOUT_S),
            )
    except NodeError as exc:
        raise JoinError(
            502,
            f'The controller could not reach this screen back at '
            f'{request.screen_host}:{request.screen_port} ({exc}). '
            'Check that the controller can reach the screen on that '
            'address and port.',
        ) from exc
    return info if isinstance(info, dict) else {}


def join(request: JoinRequest) -> tuple[Node, bool]:
    """Verify and store a joining screen. Returns (node, created).

    A screen that is already listed (same certificate) gets its token
    and address replaced, so a screen that left and joins again just
    works.
    """
    if not check_secret(request.secret):
        raise JoinError(
            403,
            'The join information is wrong or was regenerated on the '
            "controller. Copy it again from the controller's Screens page.",
        )
    info = verify_screen(request)
    with transaction.atomic():
        node, created = Node.objects.select_for_update().get_or_create(
            fingerprint=request.screen_fp,
            defaults={
                'name': request.screen_name,
                'host': request.screen_host,
                'port': request.screen_port,
                'token_ciphertext': vault.encrypt(request.token),
            },
        )
        if not created:
            node.name = request.screen_name
            node.host = request.screen_host
            node.port = request.screen_port
            node.token_ciphertext = vault.encrypt(request.token)
        node.version = str(info.get('anthias_version') or '')[:100]
        node.device_model = str(info.get('device_model') or '')[:200]
        node.status = NodeStatus.ONLINE
        node.status_detail = ''
        node.last_seen_at = timezone.now()
        node.save()
    return node, created
