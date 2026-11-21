"""Screen side of the push connection to a multi-screen controller.

While this screen is managed (``lib.management``), a background thread
keeps one WebSocket open to the controller's
``wss://<controller>:8443/api/v2/controller/ws`` and pushes its status,
so the controller does not have to poll it (the controller side, and
the message format, are in ``anthias_server.controller.push``).

* **Pinned.** The connection is checked against the controller
  fingerprint from the join information before anything is sent; a
  different certificate ends the session and the thread retries later.
* **No secret.** The screen presents the client certificate the
  controller's CA issued it at approval (with its node key), so the
  controller knows which screen is calling without a token.
* **Two-way.** The controller sends each new revocation list, and a
  renewed certificate when this screen asks for one (once per
  connection, when fewer than 30 days of validity are left).
* **Cheap.** A heartbeat every minute, health only when a warning
  appears or clears, and at least every 15 minutes. uvicorn on the
  controller and ``websockets`` here exchange pings, so a dead peer is
  noticed within about a minute either way.
* **Optional.** If the connection cannot be made (an older controller,
  a firewall), the controller simply keeps polling this screen.

Runs only in the web server process (``django_project.asgi``), and only
where Caddy is set up, like the Caddy config push it sits next to.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import ssl
import threading
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

PATH = '/api/v2/controller/ws'
HEARTBEAT_S = 60
HEALTH_PUSH_MAX_S = 15 * 60
IDLE_CHECK_S = 30
RETRY_MIN_S = 5
RETRY_MAX_S = 300
OPEN_TIMEOUT_S = 20
PING_INTERVAL_S = 30
PING_TIMEOUT_S = 30
MAX_MESSAGE_BYTES = 64 * 1024


class PinMismatchError(Exception):
    """The controller presented a certificate other than the pinned one."""


@dataclass(frozen=True)
class Target:
    controller_id: int
    uri: str
    fingerprint: str
    cert_path: str
    key_path: str


def push_uri(controller_url: str) -> str:
    parts = urlsplit(controller_url)
    if parts.scheme != 'https' or not parts.hostname:
        raise ValueError(f'Not an https controller URL: {controller_url!r}')
    return f'wss://{parts.netloc}{PATH}'


def current_target() -> Target | None:
    """Where to push, or None while this screen is not managed."""
    from django.db import close_old_connections

    from anthias_server.lib import management, node_identity

    close_old_connections()
    if not management.auth_enabled():
        return None
    controller = management.current_controller()
    if controller is None or not management.client_cert_path().exists():
        return None
    identity = node_identity.ensure_node_identity()
    return Target(
        controller_id=controller.pk,
        uri=push_uri(controller.url),
        fingerprint=controller.fingerprint,
        # The client certificate the controller's CA issued at approval
        # (renewed over this connection), with the node key.
        cert_path=str(management.client_cert_path()),
        key_path=str(identity.key_path),
    )


def renewal_request(target: Target) -> dict[str, Any] | None:
    """A renewal message if the client certificate is close to expiry."""
    from anthias_server.lib import pki

    try:
        cert = pki.load_cert(Path(target.cert_path).read_bytes())
        key = pki.load_key(Path(target.key_path).read_bytes())
    except (OSError, pki.PkiError):
        return None
    if not pki.needs_renewal(cert):
        return None
    common_name = pki.fingerprint(cert)
    from anthias_server.lib import node_identity

    try:
        common_name = node_identity.ensure_node_identity().fingerprint
    except (OSError, ValueError):
        pass
    return {
        'type': 'renew',
        'csr': pki.make_csr(key, common_name, pki.ROLE_SCREEN),
    }


def handle_message(raw: str | bytes) -> str:
    """Apply a message from the controller. Returns its type."""
    from anthias_server.lib import management

    try:
        message = json.loads(raw)
    except ValueError:
        return ''
    if not isinstance(message, dict):
        return ''
    kind = str(message.get('type') or '')
    crl, certificate = message.get('crl'), message.get('certificate')
    if kind == 'crl' and isinstance(crl, str) and management.store_crl(crl):
        logger.info('Stored a new revocation list from the controller')
    if (
        kind == 'certificate'
        and isinstance(certificate, str)
        and management.store_client_cert(certificate)
    ):
        logger.info('Stored a renewed client certificate')
    return kind


def collect_health() -> dict[str, Any]:
    """This screen's /api/v2/info health fields."""
    from django.db import close_old_connections

    from anthias_server.api.views.v2 import InfoViewV2

    close_old_connections()
    return InfoViewV2().health_payload()


def warning_flags(health: dict[str, Any]) -> frozenset[str]:
    """The health warnings the controller would show for ``health``."""
    from anthias_server.controller.health import health_from_info, problems

    return frozenset(key for key, _ in problems(health_from_info(health)))


def client_context(target: Target) -> ssl.SSLContext:
    """TLS for the push connection: our node cert, pin checked after.

    Chain verification is off because the controller's certificate is
    self-signed; :func:`check_pin` is the check that matters, and it
    runs before any message is sent.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # The pin replaces chain and name checks; see check_pin().
    context.check_hostname = False  # NOSONAR
    context.verify_mode = ssl.CERT_NONE  # NOSONAR
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(target.cert_path, target.key_path)
    return context


def check_pin(ssl_object: Any, fingerprint: str) -> None:
    der = ssl_object.getpeercert(binary_form=True) if ssl_object else None
    if not der or hashlib.sha256(der).hexdigest() != fingerprint.lower():
        raise PinMismatchError(
            "The controller's certificate does not match the one this "
            'screen joined.'
        )


async def run_session(target: Target) -> None:
    """One connection, until it drops or this screen stops being managed."""
    from websockets.asyncio.client import connect

    async with connect(
        target.uri,
        ssl=client_context(target),
        proxy=None,
        open_timeout=OPEN_TIMEOUT_S,
        ping_interval=PING_INTERVAL_S,
        ping_timeout=PING_TIMEOUT_S,
        max_size=MAX_MESSAGE_BYTES,
        user_agent_header='Anthias screen push',
    ) as ws:
        check_pin(
            ws.transport.get_extra_info('ssl_object'), target.fingerprint
        )
        health = await asyncio.to_thread(collect_health)
        await ws.send(json.dumps({'type': 'hello', 'health': health}))
        sent_flags, sent_at = warning_flags(health), monotonic()
        renewal_sent = False
        logger.info('Pushing status to the controller at %s', target.uri)
        next_beat = monotonic() + HEARTBEAT_S
        while True:
            try:
                # The controller sends CRL updates and renewed
                # certificates; reading also keeps close and ping
                # handling running. Heartbeats keep their own schedule.
                raw = await asyncio.wait_for(
                    ws.recv(), max(0.0, next_beat - monotonic())
                )
                await asyncio.to_thread(handle_message, raw)
                continue
            except TimeoutError:
                pass
            next_beat = monotonic() + HEARTBEAT_S
            if not renewal_sent:
                renewal = await asyncio.to_thread(renewal_request, target)
                if renewal is not None:
                    await ws.send(json.dumps(renewal))
                    renewal_sent = True
            still = await asyncio.to_thread(current_target)
            if still != target:
                await ws.close()
                return
            health = await asyncio.to_thread(collect_health)
            flags = warning_flags(health)
            if (
                flags != sent_flags
                or monotonic() - sent_at >= HEALTH_PUSH_MAX_S
            ):
                await ws.send(json.dumps({'type': 'health', 'health': health}))
                sent_flags, sent_at = flags, monotonic()
            else:
                await ws.send(json.dumps({'type': 'heartbeat'}))


async def run_forever() -> None:
    delay = RETRY_MIN_S
    while True:
        try:
            target = await asyncio.to_thread(current_target)
        except Exception:
            logger.exception('Cannot work out the push target')
            target = None
        if target is None:
            delay = RETRY_MIN_S
            await asyncio.sleep(IDLE_CHECK_S)
            continue
        try:
            await run_session(target)
            delay = RETRY_MIN_S
        except asyncio.CancelledError:
            raise
        except PinMismatchError as exc:
            logger.warning('Push connection refused: %s', exc)
        except Exception as exc:
            logger.info(
                'Push connection to %s ended: %s (retrying in ~%ss)',
                target.uri,
                exc,
                delay,
            )
        # Jitter for reconnect spacing, not for security.
        await asyncio.sleep(
            delay * random.uniform(0.8, 1.2)  # NOSONAR
        )
        delay = min(delay * 2, RETRY_MAX_S)


def start_background_push() -> threading.Thread | None:
    """Run :func:`run_forever` in a daemon thread with its own loop."""
    from anthias_server.lib import caddy

    if not caddy.RUN_DIR.is_dir():
        logger.info('No %s; not starting the push client.', caddy.RUN_DIR)
        return None
    thread = threading.Thread(
        target=asyncio.run,
        args=(run_forever(),),
        name='controller-push',
        daemon=True,
    )
    thread.start()
    return thread
