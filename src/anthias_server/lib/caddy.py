"""Generate Caddy's config and load it through the admin socket.

Caddy (the ``anthias-caddy`` service) owns every web port on the
screen; ``anthias-server`` has no host port of its own. This module is
the only thing that decides what Caddy serves:

* ``:80`` keeps answering ``/api/*`` over plain HTTP for one
  deprecation release, because a script that POSTs to
  ``http://screen/api/...`` breaks on a redirect (a 301 turns its POST
  into a GET). Everything else redirects to HTTPS.
* ``:443`` serves the UI and API with the web certificate
  (``lib.web_certificate``): the operator's upload, a Let's Encrypt
  certificate, or by default the node certificate.
* Extra servers from :func:`register_server_provider`. The
  machine-to-machine site on ``:8443`` plugs in here; it always uses
  the node certificate (tag ``anthias-node``), whatever the UI serves.

The config is pushed with ``POST /load`` on Caddy's admin API, which
listens on a Unix socket in the ``anthias-caddy-run`` volume. Only
``anthias-server`` and ``anthias-caddy`` mount that volume, so nothing
else on the network or in the other containers can reconfigure Caddy.
Caddy validates a config before switching to it and keeps running the
old one if it is rejected, so a bad push never takes the UI down.
Caddy starts with ``--resume`` and therefore comes back with the last
config that loaded successfully; ``anthias-server`` re-applies on every
start as well, so the two can never drift apart for long.

Certificates are copied into ``certs/`` in the same volume rather
than mounting ``~/.anthias`` into Caddy: Caddy only ever sees the
certificate and key it is serving.
"""

import http.client
import json
import logging
import os
import shutil
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anthias_server.lib import node_identity, web_certificate

logger = logging.getLogger(__name__)

RUN_DIR = Path(os.environ.get('ANTHIAS_CADDY_RUN_DIR', '/run/anthias-caddy'))
UPSTREAM = os.environ.get('ANTHIAS_CADDY_UPSTREAM', 'anthias-server:8080')

TAG_NODE = 'anthias-node'
TAG_WEB = 'anthias-web'

HTTP_PORT = 80
HTTPS_PORT = 443

# Strip from every request on the UI sites so a client cannot pretend
# to have arrived through the machine-to-machine listener.
LISTENER_HEADER = 'X-Anthias-Listener'

# Edge limits. Kept in line with start_server.sh's uvicorn guards
# (--timeout-keep-alive 30): a client gets 10 s to send its headers,
# an idle keep-alive connection is closed after 30 s. There is no read
# or write timeout on the body, and no size limit, because uploads and
# backup downloads are unbounded and can take many minutes on a Pi.
READ_HEADER_TIMEOUT = '10s'
IDLE_TIMEOUT = '30s'
MAX_HEADER_BYTES = 64 * 1024

ServerProvider = Callable[[], dict[str, dict[str, Any]]]
_server_providers: list[ServerProvider] = []


class CaddyError(Exception):
    """Caddy could not be reached."""


class CaddyConfigError(CaddyError):
    """Caddy rejected the config; it keeps running the previous one."""


def register_server_provider(provider: ServerProvider) -> None:
    """Add extra ``apps.http.servers`` entries to every generated config.

    ``provider()`` returns ``{server_name: server_json}``. It runs on
    every :func:`apply`, so it can return ``{}`` to remove its servers.
    Registering the same callable twice is a no-op.
    """
    if provider not in _server_providers:
        _server_providers.append(provider)


def unregister_server_provider(provider: ServerProvider) -> None:
    if provider in _server_providers:
        _server_providers.remove(provider)


def admin_socket() -> Path:
    return RUN_DIR / 'admin.sock'


def certs_dir() -> Path:
    return RUN_DIR / 'certs'


def proxy_handler() -> dict[str, Any]:
    """The reverse_proxy handler every UI route ends in."""
    return {
        'handler': 'reverse_proxy',
        'upstreams': [{'dial': UPSTREAM}],
        'headers': {'request': {'delete': [LISTENER_HEADER]}},
    }


def _server_limits() -> dict[str, Any]:
    return {
        'read_header_timeout': READ_HEADER_TIMEOUT,
        'idle_timeout': IDLE_TIMEOUT,
        'max_header_bytes': MAX_HEADER_BYTES,
    }


def _http_server(http_port: int) -> dict[str, Any]:
    return {
        'listen': [f':{http_port}'],
        'routes': [
            {
                'match': [{'path': ['/api/*']}],
                'handle': [proxy_handler()],
                'terminal': True,
            },
            {
                'handle': [
                    {
                        'handler': 'static_response',
                        'status_code': 308,
                        'headers': {
                            'Location': [
                                'https://{http.request.host}{http.request.uri}'
                            ]
                        },
                    }
                ],
            },
        ],
        'automatic_https': {'disable_redirects': True},
        **_server_limits(),
    }


def _https_server(
    https_port: int, state: web_certificate.WebTlsState, web_tag: str
) -> dict[str, Any]:
    policies: list[dict[str, Any]] = []
    if state.mode == web_certificate.MODE_ACME:
        policies.append({'match': {'sni': [state.domain]}})
    policies.append({'certificate_selection': {'any_tag': [web_tag]}})
    automatic: dict[str, Any] = (
        {'disable_redirects': True}
        if state.mode == web_certificate.MODE_ACME
        else {'disable': True}
    )
    return {
        'listen': [f':{https_port}'],
        'routes': [{'handle': [proxy_handler()]}],
        'tls_connection_policies': policies,
        'automatic_https': automatic,
        **_server_limits(),
    }


def build_config(
    state: web_certificate.WebTlsState | None = None,
    *,
    http_port: int = HTTP_PORT,
    https_port: int = HTTPS_PORT,
    extra_servers: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the full Caddy JSON config for the current state.

    Certificate paths point into :func:`certs_dir` as seen from inside
    the Caddy container; :func:`stage_certificates` puts them there.
    """
    state = state or web_certificate.load_state()
    node_cert = str(certs_dir() / 'node.crt')
    node_key = str(certs_dir() / 'node.key')

    # Caddy caches certificates by hash across config loads, and a
    # cached certificate keeps the tags it was first loaded with even
    # when a later config is no longer loading it. Measured on Caddy
    # 2.11: with a shared tag, switching default -> custom kept serving
    # the node certificate, and custom A -> custom B kept serving A.
    # So the node certificate only ever carries TAG_NODE, and every
    # uploaded certificate gets a tag of its own, derived from its
    # fingerprint, that no other certificate can match.
    load_files: list[dict[str, Any]] = [
        {'certificate': node_cert, 'key': node_key, 'tags': [TAG_NODE]}
    ]
    web_tag = TAG_NODE
    custom = (
        web_certificate.current_custom_info()
        if state.mode == web_certificate.MODE_CUSTOM
        else None
    )
    if custom is not None:
        web_tag = f'{TAG_WEB}-{custom.fingerprint[:16]}'
        load_files.append(
            {
                'certificate': str(certs_dir() / 'web.crt'),
                'key': str(certs_dir() / 'web.key'),
                'tags': [web_tag],
            }
        )

    tls_app: dict[str, Any] = {'certificates': {'load_files': load_files}}
    if state.mode == web_certificate.MODE_ACME:
        issuer: dict[str, Any] = {'module': 'acme'}
        if state.email:
            issuer['email'] = state.email
        if state.staging:
            issuer['ca'] = (
                'https://acme-staging-v02.api.letsencrypt.org/directory'
            )
        tls_app['certificates']['automate'] = [state.domain]
        tls_app['automation'] = {
            'policies': [{'subjects': [state.domain], 'issuers': [issuer]}]
        }

    servers: dict[str, dict[str, Any]] = {
        'anthias_http': _http_server(http_port),
        'anthias_web': _https_server(https_port, state, web_tag),
    }
    for provider in tuple(_server_providers):
        try:
            servers.update(provider())
        except Exception:
            logger.exception('Caddy server provider %r failed', provider)
    if extra_servers:
        servers.update(extra_servers)

    return {
        'admin': {
            'listen': f'unix/{admin_socket()}|0600',
            'config': {'persist': True},
        },
        'logging': {'logs': {'default': {'level': 'WARN'}}},
        'apps': {'tls': tls_app, 'http': {'servers': servers}},
    }


def _copy_private(src: Path, dst: Path, mode: int) -> None:
    tmp = dst.with_name(f'.{dst.name}.tmp')
    shutil.copyfile(src, tmp)
    os.chmod(tmp, mode)
    os.replace(tmp, dst)


def stage_certificates(state: web_certificate.WebTlsState) -> None:
    """Copy the certificates Caddy needs into the shared volume."""
    identity = node_identity.ensure_node_identity()
    certs_dir().mkdir(parents=True, exist_ok=True)
    os.chmod(certs_dir(), 0o700)
    _copy_private(identity.cert_path, certs_dir() / 'node.crt', 0o644)
    _copy_private(identity.key_path, certs_dir() / 'node.key', 0o600)
    if state.mode == web_certificate.MODE_CUSTOM:
        _copy_private(
            web_certificate.cert_path(), certs_dir() / 'web.crt', 0o644
        )
        _copy_private(
            web_certificate.key_path(), certs_dir() / 'web.key', 0o600
        )
    else:
        for name in ('web.crt', 'web.key'):
            (certs_dir() / name).unlink(missing_ok=True)


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: Path, timeout: float) -> None:
        # The host only fills in the Host header; Caddy's admin API
        # accepts any host on a Unix socket.
        super().__init__('localhost', timeout=timeout)
        self._path = str(path)

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._path)
        except OSError:
            sock.close()
            raise
        self.sock = sock


def _request(
    method: str,
    path: str,
    body: bytes | None = None,
    timeout: float = 30.0,
) -> tuple[int, bytes]:
    conn = _UnixHTTPConnection(admin_socket(), timeout)
    try:
        headers = {'Content-Type': 'application/json'} if body else {}
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return response.status, response.read()
    except OSError as exc:
        raise CaddyError(
            f'Cannot reach Caddy at {admin_socket()}: {exc}'
        ) from exc
    finally:
        conn.close()


def is_reachable() -> bool:
    try:
        status, _ = _request('GET', '/config/admin', timeout=3.0)
    except CaddyError:
        return False
    return status == 200


def load(config: dict[str, Any]) -> None:
    status, body = _request('POST', '/load', json.dumps(config).encode())
    if status != 200:
        message = body.decode(errors='replace').strip()
        try:
            message = json.loads(message).get('error', message)
        except ValueError:
            pass
        raise CaddyConfigError(message or f'HTTP {status}')


@dataclass(frozen=True)
class ApplyResult:
    mode: str
    servers: tuple[str, ...]


_apply_lock = threading.Lock()


def apply() -> ApplyResult:
    """Render the config for the current state and load it into Caddy.

    Idempotent; safe to call on every settings change. Raises
    :class:`CaddyError` when Caddy is unreachable and
    :class:`CaddyConfigError` when it rejects the config, in which
    case Caddy keeps serving the previous one.
    """
    with _apply_lock:
        state = web_certificate.load_state()
        stage_certificates(state)
        config = build_config(state)
        load(config)
        servers = tuple(sorted(config['apps']['http']['servers']))
        logger.info(
            'Loaded Caddy config (web certificate: %s, servers: %s)',
            state.mode,
            ', '.join(servers),
        )
        return ApplyResult(mode=state.mode, servers=servers)


def apply_change(change: Callable[[], None]) -> ApplyResult:
    """Make a web-certificate change on disk, then apply it.

    If Caddy rejects the result, or cannot be reached, the files are
    put back the way they were (and re-applied, best effort) before the
    error propagates. Either the new certificate is live, or nothing
    changed.
    """
    saved = web_certificate.snapshot()
    change()
    try:
        return apply()
    except CaddyError:
        web_certificate.restore(saved)
        try:
            apply()
        except CaddyError:
            logger.warning('Could not re-apply the previous Caddy config')
        raise


def apply_on_startup(
    attempts: int = 60,
    delay: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Migrate the old SSL layout, then push the config to Caddy.

    Caddy may still be starting when the server comes up, so this
    retries for a couple of minutes. A rejected config is not retried:
    it will not get better by waiting.
    """
    try:
        web_certificate.migrate_legacy_layout()
    except Exception:
        logger.exception('Migrating the old HTTPS setup failed')
    for attempt in range(1, attempts + 1):
        try:
            apply()
            return True
        except CaddyConfigError as exc:
            logger.error('Caddy rejected the generated config: %s', exc)
            return False
        except CaddyError as exc:
            if attempt == attempts:
                logger.warning('Giving up on Caddy: %s', exc)
                return False
            sleep(delay)
        except Exception:
            logger.exception('Preparing the Caddy config failed')
            return False
    return False


def start_background_apply() -> threading.Thread | None:
    """Run :func:`apply_on_startup` off the server's startup path.

    Does nothing when the admin socket's directory is not mounted
    (development and test setups that run without Caddy).
    """
    if not RUN_DIR.is_dir():
        logger.info('No %s; not configuring Caddy.', RUN_DIR)
        return None
    thread = threading.Thread(
        target=apply_on_startup, name='caddy-apply', daemon=True
    )
    thread.start()
    return thread
