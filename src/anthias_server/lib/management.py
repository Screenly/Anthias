"""Screen side of multi-screen management: the m2m site and joining.

A controller (another Anthias in controller mode) manages this screen over
the existing v2 REST API. Joining works like a Proxmox cluster join:
the controller's Screens page shows a *join information* snippet, the
operator pastes it into this screen's Settings, and the screen calls
the controller. The snippet carries the controller's certificate
fingerprint, so the screen's very first connection is already pinned.
There is no trust-on-first-use step and no unpinned connection.

**The m2m site.** While ``management_enabled`` (or ``controller_mode``
on a controller) is set, Caddy serves a dedicated site on ``:8443``
that always presents this node's self-signed certificate
(:mod:`anthias_server.lib.node_identity`) and only proxies
``/api/v2/*``. Peers pin that certificate's SHA-256 fingerprint, so
the user-facing web certificate (which the operator may replace) never
affects a pairing. The site is contributed to the Caddy config by
:func:`caddy_extra_servers`, registered with
:mod:`anthias_server.lib.caddy` at app start, and applied live over
the Caddy admin socket. Requests that come through it carry
``X-Anthias-Listener: m2m``. The UI sites strip that header, so its
presence means "came in on :8443".

Management requires authentication. With ``auth_backend == ''`` the
API is open to the LAN by contract and a controller token would
protect nothing, so the site is withdrawn while auth is off, whatever
the stored flags say, and joining is refused.

**Joining** (:func:`join`):

1. Parse the snippet: ``anthias-join:`` + base64url(JSON
   ``{"v": 1, "url", "fp", "secret", "name"}``).
2. Mint an :class:`~anthias_server.app.models.ApiToken` named
   ``Controller: <name>``.
3. Turn the m2m site on, because the controller calls back to
   ``/api/v2/info`` on it with that token before it answers.
4. POST ``{url}/api/v2/controller/join`` over a session pinned to
   ``fp``.
5. On 201, record a
   :class:`~anthias_server.app.models.ManagementController`. On
   anything else, revoke the token and restore the site to how it was.

The screen keeps only the token's hash, as for every API token, so it
never holds a credential for the controller. The screen counts as
managed while the controller's token is active. Revoking it from the
API-tokens list therefore also ends management, and the controller
notices the next time it polls (401).

**Leaving** (:func:`leave_management` locally,
``DELETE /api/v2/pairing`` from the controller) revokes the token,
drops the record and turns the m2m site off. v1 has one controller per
screen.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    import requests
    from django.db.models import QuerySet

    from anthias_server.app.models import ManagementController

logger = logging.getLogger(__name__)

# Set while join() waits for the controller to call back on :8443,
# before a ManagementController row exists. Without it the site would
# have to trust the management_enabled flag alone.
_join_in_progress = threading.Event()

M2M_PORT = 8443
M2M_SERVER_NAME = 'anthias_m2m'
M2M_LISTENER_VALUE = 'm2m'

JOIN_PREFIX = 'anthias-join:'
JOIN_PATH = '/api/v2/controller/join'
JOIN_TIMEOUT = (10, 60)
CONTROLLER_TOKEN_PREFIX = 'Controller: '
MAX_CONTROLLER_NAME_LEN = 80
MAX_HOST_LEN = 253
# How long this screen waits for its join request to be approved (the
# controller drops requests after 24 hours).
PENDING_TTL = timedelta(hours=25)


class ManagementError(ValueError):
    """Operator-facing reason a management action was refused."""


@dataclass(frozen=True)
class JoinInfo:
    url: str
    fingerprint: str
    secret: str
    name: str


# --- helpers ---------------------------------------------------------------


def _settings() -> Any:
    from anthias_server.settings import settings

    return settings


def node_fingerprint() -> str:
    """This node's ID: lowercase hex SHA-256 of its certificate DER.

    Creates the node identity on first use. Raises ManagementError if
    it can't, so callers refuse to join rather than serve a site with
    no certificate.
    """
    from anthias_server.lib import node_identity

    try:
        return node_identity.ensure_node_identity().fingerprint
    except (OSError, ValueError) as exc:
        logger.exception('Node identity unavailable')
        raise ManagementError(
            "Could not create this screen's identity certificate."
        ) from exc


def device_name() -> str:
    """How this screen introduces itself to a controller.

    The player name when one is set. Otherwise a stable label from the
    node fingerprint: inside the container the hostname is the
    container ID, which changes on every recreate and means nothing to
    the operator.
    """
    name = str(_settings()['player_name'] or '').strip()
    if name:
        return name
    return f'Anthias {node_fingerprint()[:8]}'


# --- state -----------------------------------------------------------------


def auth_enabled() -> bool:
    return bool(_settings()['auth_backend'])


def management_enabled() -> bool:
    return bool(_settings()['management_enabled'])


def m2m_site_active() -> bool:
    """Whether Caddy should serve :8443.

    On while this node is a controller (``controller_mode``),
    or while it is managed: ``management_enabled`` and a
    controller whose token is still active (or a join waiting for the
    controller's callback). Never while auth is off.

    The flag alone is not enough. It lives in ``anthias.conf`` and can
    outlive the pairing, e.g. when the token was revoked through a path
    that doesn't touch the flag; trusting it would reopen :8443 on the
    next start for a screen that no longer has a controller.
    """
    settings = _settings()
    if not auth_enabled():
        return False
    if settings['controller_mode']:
        return True
    if not settings['management_enabled']:
        return False
    return (
        _join_in_progress.is_set()
        or is_managed()
        or pending_controller() is not None
    )


def _active_controllers() -> QuerySet[ManagementController]:
    from anthias_server.app.models import ManagementController

    return (
        ManagementController.objects.select_related('token')
        .filter(token__revoked_at__isnull=True)
        .order_by('-paired_at')
    )


def current_controller() -> ManagementController | None:
    """The controller this screen is joined to (approved, token active)."""
    return _active_controllers().filter(approved=True).first()


def pending_controller() -> ManagementController | None:
    """A join request still waiting for the operator's approval.

    Requests expire on the controller after a day; past that (plus a
    margin) the screen stops waiting too.
    """
    from django.utils import timezone

    cutoff = timezone.now() - PENDING_TTL
    return (
        _active_controllers()
        .filter(approved=False, paired_at__gte=cutoff)
        .first()
    )


def is_managed() -> bool:
    return current_controller() is not None


def check_code() -> str:
    """This screen's check code, as the controller shows it."""
    from anthias_server.lib import pki

    return pki.check_code(node_fingerprint())


def _set_management_enabled(enabled: bool) -> None:
    settings = _settings()
    # Re-read first: another process (the CLI, a shell, a second
    # worker) may have changed the file, and comparing against a stale
    # in-memory copy would skip a write the file still needs.
    settings.load()
    if bool(settings['management_enabled']) != enabled:
        settings['management_enabled'] = enabled
        settings.save()


def apply_caddy() -> bool:
    """Push the Caddy config. Never raises.

    A Caddy that is down picks the site up when the server re-applies
    its config on the next start.
    """
    from anthias_server.lib import caddy

    try:
        caddy.apply()
    except Exception:
        logger.exception('Could not apply the Caddy config')
        return False
    return True


# --- join ------------------------------------------------------------------


def parse_join_info(raw: str) -> JoinInfo:
    """Decode a pasted join snippet. Raises ManagementError."""
    from anthias_server.lib.pinned_http import (
        PinnedFingerprintError,
        normalize_fingerprint,
    )

    text = ''.join((raw or '').split())
    if not text.startswith(JOIN_PREFIX):
        raise ManagementError(
            'That is not Anthias join information. Copy it from the '
            "controller's Screens page; it starts with "
            f'"{JOIN_PREFIX}".'
        )
    body = text[len(JOIN_PREFIX) :]
    try:
        padded = body + '=' * (-len(body) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode('ascii')))
    except (ValueError, binascii.Error, UnicodeError) as exc:
        raise ManagementError(
            'The join information is incomplete or damaged. Copy it '
            'again from the controller.'
        ) from exc
    if not isinstance(data, dict) or data.get('v') != 1:
        raise ManagementError(
            'This join information is from a different Anthias version.'
        )

    url = str(data.get('url') or '').strip().rstrip('/')
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname:
        raise ManagementError('The join information has no https address.')
    try:
        fingerprint = normalize_fingerprint(str(data.get('fp') or ''))
    except PinnedFingerprintError as exc:
        raise ManagementError(
            'The join information has no valid certificate fingerprint.'
        ) from exc
    secret = str(data.get('secret') or '')
    if not secret:
        raise ManagementError('The join information has no secret.')
    name = ' '.join(str(data.get('name') or '').split())
    name = (name or parts.hostname)[:MAX_CONTROLLER_NAME_LEN]
    return JoinInfo(url=url, fingerprint=fingerprint, secret=secret, name=name)


def _clean_screen_host(raw: str) -> str | None:
    host = (raw or '').strip()
    if not host:
        return None
    if (
        len(host) > MAX_HOST_LEN
        or any(c.isspace() for c in host)
        or '/' in host
    ):
        raise ManagementError(
            "Enter this screen's address as an IP address or DNS name."
        )
    return host


def _join_failure_reason(response: requests.Response) -> str:
    try:
        detail = response.json().get('detail')
    except (ValueError, AttributeError):
        detail = None
    if response.status_code == 403:
        return (
            'The controller rejected the join information. It may have '
            'been regenerated; copy it again.'
        )
    if detail:
        return f'The controller refused to add this screen: {detail}'
    return (
        'The controller refused to add this screen '
        f'(HTTP {response.status_code}).'
    )


def join(snippet: str, screen_host: str = '') -> ManagementController:
    """Join the controller described by ``snippet``.

    Raises ManagementError with an operator-facing reason. Every
    failure leaves the screen as it was: no extra active token, and
    the m2m site back to its previous state.
    """
    import requests

    from anthias_server.app.models import ManagementController
    from anthias_server.lib import api_tokens
    from anthias_server.lib.auth import _persisted_operator
    from anthias_server.lib.pinned_http import pinned_session

    if not auth_enabled():
        raise ManagementError(
            'Turn on authentication before joining a controller. '
            'While authentication is disabled the API is open to '
            'anyone on the network, so a controller token would not '
            'protect it.'
        )
    if _settings()['controller_mode']:
        raise ManagementError(
            'This device is a controller. A controller cannot also be '
            'managed by another one; turn off Controller mode first.'
        )
    existing = current_controller()
    if existing is not None:
        raise ManagementError(
            f'This screen is already managed by {existing.name}. '
            'Leave management first.'
        )
    info = parse_join_info(snippet)
    host = _clean_screen_host(screen_host)
    own_fp = node_fingerprint()
    operator = _persisted_operator()
    if operator is None:
        raise ManagementError('Create an operator account first.')
    # A new request replaces one still waiting (for this or another
    # controller): only one controller at a time.
    earlier = pending_controller()
    if earlier is not None:
        _revoke_and_forget([earlier])
    screen_cert, csr = _join_credentials(own_fp)

    token, plaintext = api_tokens.create_token(
        operator, f'{CONTROLLER_TOKEN_PREFIX}{info.name}'
    )
    was_enabled = management_enabled()
    _set_management_enabled(True)
    _join_in_progress.set()
    apply_caddy()

    payload: dict[str, Any] = {
        'secret': info.secret,
        'screen_name': device_name(),
        'screen_fp': own_fp,
        'screen_port': M2M_PORT,
        'token': plaintext,
        'screen_cert': screen_cert,
        'csr': csr,
    }
    if host:
        payload['screen_host'] = host

    try:
        try:
            with pinned_session(info.fingerprint) as session:
                response = session.post(
                    info.url + JOIN_PATH,
                    json=payload,
                    timeout=JOIN_TIMEOUT,
                    allow_redirects=False,
                )
        except requests.exceptions.SSLError as exc:
            logger.warning('Join %s: TLS/pin failure: %s', info.url, exc)
            raise ManagementError(
                "The controller's certificate does not match the join "
                'information, so this screen did not connect. Check the '
                'address, or copy the join information again.'
            ) from exc
        except requests.exceptions.RequestException as exc:
            logger.warning('Join %s: connection failed: %s', info.url, exc)
            raise ManagementError(
                f'Could not reach the controller at {info.url}.'
            ) from exc
        if response.status_code != 202:
            logger.warning(
                'Join %s refused: HTTP %s', info.url, response.status_code
            )
            raise ManagementError(_join_failure_reason(response))
        # The controller has listed our request, so keep the token even
        # if its body is unreadable; the snippet's name stands in for
        # the one it would have confirmed.
        try:
            confirmed = response.json()
        except ValueError:
            confirmed = None
        if not isinstance(confirmed, dict):
            logger.warning('Join %s: unreadable 202 body', info.url)
            confirmed = {}
    except ManagementError:
        _join_in_progress.clear()
        api_tokens.revoke_token(token.pk)
        _set_management_enabled(was_enabled)
        apply_caddy()
        raise

    name = ' '.join(str(confirmed.get('controller_name') or '').split())
    try:
        controller = ManagementController.objects.create(
            name=(name or info.name)[:MAX_CONTROLLER_NAME_LEN],
            url=info.url,
            fingerprint=info.fingerprint,
            token=token,
            approved=False,
            request_id=str(confirmed.get('request_id') or '')[:64],
        )
    finally:
        _join_in_progress.clear()
    logger.info(
        'Asked to join controller %r at %s; waiting for approval',
        controller.name,
        info.url,
    )
    return controller


def _join_credentials(own_fp: str) -> tuple[str, str]:
    """This screen's node certificate and a CSR for the same key."""
    from anthias_server.lib import node_identity, pki

    identity = node_identity.ensure_node_identity()
    key = pki.load_key(identity.key_path.read_bytes())
    return (
        identity.cert_path.read_text(),
        pki.make_csr(key, own_fp, pki.ROLE_SCREEN),
    )


# --- approval and certificates ----------------------------------------------
#
# Once the operator approves the join request, the controller calls
# POST /api/v2/pairing/certificate with the client certificate its CA
# issued for this screen, its CA certificate and its CRL. From then on
# this screen's :8443 site requires a client certificate from that CA
# (Caddy), and m2m requests must present the controller's certificate,
# not revoked (M2MClientCertMiddleware). The files sit next to the node
# certificate and, like it, are only in a backup when the operator adds
# the encrypted device keys (lib/identity_backup.py).


def managed_dir() -> Path:
    from anthias_server.lib import node_identity

    return node_identity.node_dir()


def client_cert_path() -> Path:
    return managed_dir() / 'managed-client.crt'


def controller_ca_path() -> Path:
    return managed_dir() / 'managed-ca.crt'


def crl_path() -> Path:
    return managed_dir() / 'managed-crl.pem'


def _managed_files() -> tuple[Path, Path, Path]:
    return client_cert_path(), controller_ca_path(), crl_path()


def _write_public(target: Path, text: str) -> None:
    tmp = target.with_name(f'.{target.name}.tmp')
    tmp.write_text(text)
    # Public material only (certificates, CRL); the key never goes here.
    # World-readable is intended: no secrets in these files.
    os.chmod(tmp, 0o644)  # NOSONAR(S2612)
    os.replace(tmp, target)


def _node_public_key() -> Any:
    from anthias_server.lib import node_identity, pki

    identity = node_identity.ensure_node_identity()
    return pki.load_key(identity.key_path.read_bytes()).public_key()


def controller_ca() -> Any:
    from anthias_server.lib import pki

    try:
        return pki.load_cert(controller_ca_path().read_bytes())
    except (OSError, pki.PkiError):
        return None


def install_approval(
    token_id: int,
    certificate: str,
    ca_certificate: str,
    crl: str,
    controller_name: str = '',
) -> ManagementController:
    """Accept the controller's approval: this screen's certificate.

    Only the controller holding the token from our pending request can
    deliver it, over the pinned connection it already uses. Raises
    ManagementError if anything does not check out.
    """
    from anthias_server.app.models import ManagementController
    from anthias_server.lib import pki

    controller = _active_controllers().filter(token_id=token_id).first()
    if not isinstance(controller, ManagementController):
        raise ManagementError('This token does not belong to a join request.')
    try:
        ca = pki.load_cert(ca_certificate)
        pki.check_ca(ca)
        cert = pki.load_cert(certificate)
        pki.check_client_cert(
            cert, ca, pki.ROLE_SCREEN, public_key=_node_public_key()
        )
        revocations = pki.load_crl(crl)
        pki.check_crl(revocations, ca)
    except pki.PkiError as exc:
        raise ManagementError(f'The approval was not valid: {exc}') from exc
    if cert.serial_number in pki.revoked_serials(revocations):
        raise ManagementError('The certificate delivered is already revoked.')
    _write_public(controller_ca_path(), pki.cert_pem(ca))
    _write_public(client_cert_path(), pki.cert_pem(cert))
    _write_public(crl_path(), pki.crl_pem(revocations))
    name = ' '.join(controller_name.split())[:MAX_CONTROLLER_NAME_LEN]
    controller.approved = True
    if name:
        controller.name = name
    controller.save(update_fields=['approved', 'name'])
    _set_management_enabled(True)
    apply_caddy()
    logger.info('Approved by controller %r', controller.name)
    return controller


def store_crl(text: str) -> bool:
    """Keep a newer CRL from the controller. True if it was stored."""
    from anthias_server.lib import pki

    ca = controller_ca()
    if ca is None:
        return False
    try:
        crl = pki.load_crl(text)
        pki.check_crl(crl, ca)
    except pki.PkiError as exc:
        logger.warning('Ignoring a revocation list: %s', exc)
        return False
    current = _load_crl()
    if current is not None and pki.crl_number(crl) < pki.crl_number(current):
        return False
    _write_public(crl_path(), pki.crl_pem(crl))
    return True


def store_client_cert(text: str) -> bool:
    """Keep a renewed client certificate. True if it was stored."""
    from anthias_server.lib import pki

    ca = controller_ca()
    if ca is None:
        return False
    try:
        cert = pki.load_cert(text)
        pki.check_client_cert(
            cert, ca, pki.ROLE_SCREEN, public_key=_node_public_key()
        )
    except pki.PkiError as exc:
        logger.warning('Ignoring a renewed certificate: %s', exc)
        return False
    _write_public(client_cert_path(), pki.cert_pem(cert))
    return True


def _load_crl() -> Any:
    from anthias_server.lib import pki

    try:
        return pki.load_crl(crl_path().read_bytes())
    except (OSError, pki.PkiError):
        return None


def m2m_caller_allowed(request: Any) -> tuple[bool, str]:
    """Whether an m2m request may reach this screen's API.

    Only enforced once this screen is managed: the caller must present
    a controller certificate from its controller's CA (Caddy already
    checked the chain) that is valid and not on the CRL. Before that
    (joining, waiting for approval) the bearer token is the gate, as
    for any API client. A controller's own :8443 site is not checked
    here; the join endpoint and the push consumer check their callers.
    """
    import base64

    from cryptography import x509

    from anthias_server.lib import caddy, pki

    if not request_via_m2m(request) or _settings()['controller_mode']:
        return True, ''
    if current_controller() is None:
        return True, ''
    ca = controller_ca()
    if ca is None:
        return False, 'This screen has no certificate for its controller.'
    raw = request.headers.get(caddy.CLIENT_CERT_HEADER, '')
    try:
        cert = x509.load_der_x509_certificate(base64.b64decode(raw))
        pki.check_client_cert(cert, ca, pki.ROLE_CONTROLLER)
    except ValueError:  # binascii.Error and PkiError are ValueErrors
        return False, "Only this screen's controller may call it here."
    revocations = _load_crl()
    if revocations is None:
        return False, 'This screen has no revocation list from its controller.'
    if cert.serial_number in pki.revoked_serials(revocations):
        return False, 'That controller certificate has been revoked.'
    return True, ''


class M2MClientCertMiddleware:
    """Refuse m2m requests that fail :func:`m2m_caller_allowed`."""

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: Any) -> Any:
        from django.http import JsonResponse

        allowed, reason = m2m_caller_allowed(request)
        if not allowed:
            logger.warning('Refused an m2m request: %s', reason)
            return JsonResponse({'detail': reason}, status=403)
        return self.get_response(request)


# --- leave -----------------------------------------------------------------


def _revoke_and_forget(controllers: list[ManagementController]) -> None:
    from django.utils import timezone

    for controller in controllers:
        if controller.token.revoked_at is None:
            controller.token.revoked_at = timezone.now()
            controller.token.save(update_fields=['revoked_at'])
        controller.delete()
    for path in _managed_files():
        path.unlink(missing_ok=True)
    _set_management_enabled(False)
    apply_caddy()


def leave_management() -> bool:
    """Revoke the controller's token and forget it. False if unmanaged."""
    from anthias_server.app.models import ManagementController

    controllers = list(ManagementController.objects.select_related('token'))
    was_managed = any(c.token.revoked_at is None for c in controllers)
    if controllers or management_enabled():
        _revoke_and_forget(controllers)
        logger.info('Left management')
    return was_managed


def unpair_token(token_id: int) -> bool:
    """Controller-initiated leave: only for the token that pairs us."""
    from anthias_server.app.models import ManagementController

    controllers = list(
        ManagementController.objects.select_related('token').filter(
            token_id=token_id, token__revoked_at__isnull=True
        )
    )
    if not controllers:
        return False
    _revoke_and_forget(controllers)
    logger.info('Controller %r removed this screen', controllers[0].name)
    return True


def sync_state() -> None:
    """Re-apply Caddy after something m2m_site_active() reads changed."""
    apply_caddy()


# --- Caddy -----------------------------------------------------------------


def m2m_server() -> dict[str, Any]:
    """Caddy JSON for the :8443 API-only site with the node cert.

    Caddy's reverse_proxy sets X-Forwarded-For to the peer address,
    which the controller uses as a joining screen's host when the
    operator gave none. The connection limits match the UI sites.
    """
    from anthias_server.lib import caddy

    proxy = {
        'handler': 'reverse_proxy',
        'upstreams': [{'dial': caddy.UPSTREAM}],
        'headers': {
            'request': {
                'set': {
                    caddy.LISTENER_HEADER: [M2M_LISTENER_VALUE],
                    # Empty when the peer sent no client certificate.
                    caddy.CLIENT_CERT_HEADER: [
                        '{http.request.tls.client.certificate_der_base64}'
                    ],
                }
            }
        },
    }
    return {
        'listen': [f':{M2M_PORT}'],
        'automatic_https': {'disable': True},
        'tls_connection_policies': [
            {
                'certificate_selection': {'any_tag': [caddy.TAG_NODE]},
                'client_authentication': _client_authentication(),
            }
        ],
        'routes': [
            {
                'match': [{'path': ['/api/v2/*']}],
                'handle': [proxy],
                'terminal': True,
            },
            {'handle': [{'handler': 'static_response', 'status_code': 404}]},
        ],
        # Caddy turns strict SNI/Host matching on whenever client
        # authentication is configured, and answers 421 when they
        # differ. Peers reach this site by IP address, which sends no
        # SNI, so every request (join, REST, push) would be refused.
        # Off is safe here: one site, and identity comes from the
        # per-connection client certificate, not the host name.
        'strict_sni_host': False,
        'read_header_timeout': caddy.READ_HEADER_TIMEOUT,
        'idle_timeout': caddy.IDLE_TIMEOUT,
        'max_header_bytes': caddy.MAX_HEADER_BYTES,
    }


def _inline_ca(cert: Any) -> dict[str, Any]:
    import base64

    from cryptography.hazmat.primitives import serialization

    der = cert.public_bytes(serialization.Encoding.DER)
    return {
        'provider': 'inline',
        'trusted_ca_certs': [base64.b64encode(der).decode('ascii')],
    }


def _client_authentication() -> dict[str, Any]:
    """TLS client-certificate policy for :8443 (mutual TLS).

    * A controller verifies certificates against its own CA when one
      is given; joining screens have none yet, and the join endpoint
      and push consumer check their callers themselves.
    * A managed screen requires a certificate from its controller's CA,
      so nothing else on the network gets past the TLS handshake.
    * A screen still joining or waiting for approval only asks for one.
    """
    if _settings()['controller_mode']:
        from anthias_server.controller import ca

        try:
            return {
                'mode': 'verify_if_given',
                'ca': _inline_ca(ca.ca_cert()),
            }
        except Exception:
            logger.exception('Controller CA unavailable')
            return {'mode': 'request'}
    controller_cert = controller_ca()
    if controller_cert is not None and is_managed():
        return {
            'mode': 'require_and_verify',
            'ca': _inline_ca(controller_cert),
        }
    return {'mode': 'request'}


def caddy_extra_servers() -> dict[str, dict[str, Any]]:
    """Server provider for lib.caddy: the m2m site while active."""
    if not m2m_site_active():
        return {}
    return {M2M_SERVER_NAME: m2m_server()}


def register_caddy_provider() -> None:
    from anthias_server.lib import caddy

    caddy.register_server_provider(caddy_extra_servers)


def request_via_m2m(request: Any) -> bool:
    """True when the request came in through the :8443 site."""
    import hmac

    from anthias_server.lib import caddy

    value = request.headers.get(caddy.LISTENER_HEADER, '')
    return hmac.compare_digest(value, M2M_LISTENER_VALUE)


def request_via_ui(request: Any) -> bool:
    """True when the request came in through the web interface (80/443)."""
    import hmac

    from anthias_server.lib import caddy

    value = request.headers.get(caddy.LISTENER_HEADER, '')
    return hmac.compare_digest(value, caddy.UI_LISTENER_VALUE)


# --- the web interface of a managed screen ----------------------------------
#
# Once a screen is managed, its controller is the only place to change
# it: anything edited locally is replaced on the next publish, and
# every open web interface is one more way in. So the web interface
# (ports 80/443) is shut: every page leads to a single "Managed by …"
# page, and the local REST API answers 403. What stays:
#
# * signing in, and that page, which lets a signed-in operator leave
#   management if the controller is gone (there is no other way back
#   without a shell);
# * static files, for those two pages;
# * everything on 8443 (the controller) and everything that reaches
#   anthias-server inside the Docker network (the viewer, the webview).
#
# Waiting for approval does not count: the operator still needs
# Settings to see the check code or cancel the request.


def ui_locked() -> bool:
    """Whether this screen's web interface is shut (it is managed)."""
    if _settings()['controller_mode']:
        return False
    return current_controller() is not None


def _lockdown_open_paths() -> frozenset[str]:
    from django.urls import reverse

    return frozenset(
        reverse(f'anthias_app:{name}')
        for name in ('login', 'managed', 'managed_leave')
    )


class ManagedUILockdownMiddleware:
    """Shut the web interface of a managed screen (see above)."""

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: Any) -> Any:
        from django.http import JsonResponse
        from django.shortcuts import redirect
        from django.urls import reverse

        if (
            not request_via_ui(request)
            or request.path in _lockdown_open_paths()
            or request.path.startswith('/static/')
            or not ui_locked()
        ):
            return self.get_response(request)
        if request.path.startswith('/api/'):
            return JsonResponse(
                {
                    'detail': 'This screen is managed by a multi-screen '
                    'controller. Use the controller to change it.'
                },
                status=403,
            )
        return redirect(reverse('anthias_app:managed'))
