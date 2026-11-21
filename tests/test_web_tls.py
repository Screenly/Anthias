"""Node identity, the uploadable web certificate, and the Caddy config.

The conftest fixture ``_isolate_tls_files`` points the certificate
directory and Caddy's run directory at a per-test temp dir, so nothing
here touches ``~/.anthias`` or a real Caddy.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import stat
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer
from typing import Any
from unittest import mock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import Client
from django.urls import reverse

from anthias_server.lib import (
    backup_helper,
    caddy,
    node_identity,
    web_certificate,
)

NOW = datetime.datetime.now(datetime.UTC)


# --------------------------------------------------------------------
# Certificate factory
# --------------------------------------------------------------------


def _key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _cert(
    subject: str,
    key: Any,
    issuer: str | None = None,
    issuer_key: Any = None,
    *,
    not_before: datetime.datetime | None = None,
    not_after: datetime.datetime | None = None,
    ca: bool = False,
    sans: list[x509.GeneralName] | None = None,
) -> x509.Certificate:
    builder = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
        )
        .issuer_name(
            x509.Name(
                [x509.NameAttribute(NameOID.COMMON_NAME, issuer or subject)]
            )
        )
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before or NOW - datetime.timedelta(days=1))
        .not_valid_after(not_after or NOW + datetime.timedelta(days=90))
        .add_extension(
            x509.BasicConstraints(ca=ca, path_length=None), critical=True
        )
    )
    if sans:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(sans), critical=False
        )
    return builder.sign(issuer_key or key, hashes.SHA256())


def _pem(certificate: x509.Certificate) -> bytes:
    return certificate.public_bytes(serialization.Encoding.PEM)


def _key_pem(key: Any, password: bytes | None = None) -> bytes:
    encryption: serialization.KeySerializationEncryption = (
        serialization.BestAvailableEncryption(password)
        if password
        else serialization.NoEncryption()
    )
    data: bytes = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        encryption,
    )
    return data


def _chain() -> tuple[bytes, bytes, bytes, bytes]:
    """(leaf, intermediate, root, leaf key) PEMs."""
    root_key, inter_key, leaf_key = _key(), _key(), _key()
    root = _cert('Root CA', root_key, ca=True)
    inter = _cert('Intermediate', inter_key, 'Root CA', root_key, ca=True)
    leaf = _cert(
        'screen.example.com',
        leaf_key,
        'Intermediate',
        inter_key,
        sans=[x509.DNSName('screen.example.com')],
    )
    return _pem(leaf), _pem(inter), _pem(root), _key_pem(leaf_key)


def _self_signed(name: str = 'screen.example.com') -> tuple[bytes, bytes]:
    key = _key()
    certificate = _cert(name, key, sans=[x509.DNSName(name)])
    return _pem(certificate), _key_pem(key)


# --------------------------------------------------------------------
# Node identity
# --------------------------------------------------------------------


def test_node_identity_is_generated_once() -> None:
    first = node_identity.ensure_node_identity()
    second = node_identity.ensure_node_identity()

    assert first == second
    assert first.cert_path.is_file()
    assert stat.S_IMODE(first.key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(first.cert_path.parent.stat().st_mode) == 0o700


def test_node_fingerprint_is_lowercase_sha256_of_der() -> None:
    identity = node_identity.ensure_node_identity()
    certificate = x509.load_pem_x509_certificate(
        identity.cert_path.read_bytes()
    )
    der = certificate.public_bytes(serialization.Encoding.DER)

    assert identity.fingerprint == hashlib.sha256(der).hexdigest()
    assert node_identity.fingerprint() == identity.fingerprint


def test_node_certificate_is_long_lived_ecdsa_p256() -> None:
    node_identity.ensure_node_identity()
    certificate = node_identity.certificate()
    lifetime = (
        certificate.not_valid_after_utc - certificate.not_valid_before_utc
    )
    public_key = certificate.public_key()

    assert isinstance(public_key, ec.EllipticCurvePublicKey)
    assert public_key.curve.name == 'secp256r1'
    assert lifetime >= datetime.timedelta(days=3650)


def test_node_identity_replaces_a_key_that_does_not_match() -> None:
    first = node_identity.ensure_node_identity()
    _, other_key = _self_signed()
    first.key_path.write_bytes(other_key)

    second = node_identity.ensure_node_identity()

    assert second.fingerprint != first.fingerprint
    certificate = node_identity.certificate()
    key = serialization.load_pem_private_key(
        second.key_path.read_bytes(), None
    )
    assert certificate.public_key() == key.public_key()


def test_node_common_name_fits_rfc5280_limit() -> None:
    with mock.patch('socket.gethostname', return_value='x' * 200):
        cert_pem, _ = node_identity.generate()
    certificate = x509.load_pem_x509_certificate(cert_pem)
    cn = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)

    assert len(str(cn[0].value)) == 64


def test_ssl_directory_is_never_part_of_a_backup() -> None:
    # A restored backup must not clone one screen's identity onto
    # another, nor carry a private key off the device.
    for member in (
        '.anthias/ssl',
        '.anthias/ssl/node/key.pem',
        '.anthias/ssl/web/key.pem',
        '.anthias/ssl/web/state.json',
    ):
        assert not backup_helper._is_runtime_member(member), member


# --------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------


def test_validate_accepts_a_self_signed_certificate() -> None:
    cert_pem, key_pem = _self_signed()

    result = web_certificate.validate(cert_pem, key_pem)

    assert result.info.subject == 'CN=screen.example.com'
    assert result.info.sans == ['screen.example.com']
    assert result.info.self_signed is True
    assert result.info.chain_length == 1


def test_validate_accepts_chain_in_order_from_either_input() -> None:
    leaf, inter, root, key = _chain()

    separate = web_certificate.validate(leaf, key, inter + root)
    fullchain = web_certificate.validate(leaf + inter + root, key)

    assert separate.fullchain_pem == fullchain.fullchain_pem
    assert separate.info.chain_length == 3
    assert separate.info.issuer == 'CN=Intermediate'
    assert separate.info.self_signed is False


def test_validate_rejects_chain_out_of_order() -> None:
    leaf, inter, root, key = _chain()

    with pytest.raises(web_certificate.CertificateError, match='order'):
        web_certificate.validate(leaf, key, root + inter)


def test_validate_rejects_forged_issuer_with_matching_name() -> None:
    leaf, _, _, key = _chain()
    impostor = _cert('Intermediate', _key(), ca=True)

    with pytest.raises(web_certificate.CertificateError, match='signed'):
        web_certificate.validate(leaf, key, _pem(impostor))


def test_validate_rejects_key_that_does_not_match() -> None:
    cert_pem, _ = _self_signed()
    _, other_key = _self_signed()

    with pytest.raises(web_certificate.CertificateError, match='belong'):
        web_certificate.validate(cert_pem, other_key)


def test_validate_rejects_expired_certificate() -> None:
    key = _key()
    expired = _cert(
        'old.example.com',
        key,
        not_before=NOW - datetime.timedelta(days=100),
        not_after=NOW - datetime.timedelta(days=1),
    )

    with pytest.raises(web_certificate.CertificateError, match='expired'):
        web_certificate.validate(_pem(expired), _key_pem(key))


def test_validate_rejects_not_yet_valid_certificate() -> None:
    key = _key()
    future = _cert(
        'new.example.com',
        key,
        not_before=NOW + datetime.timedelta(days=2),
        not_after=NOW + datetime.timedelta(days=100),
    )

    with pytest.raises(web_certificate.CertificateError, match='not valid'):
        web_certificate.validate(_pem(future), _key_pem(key))


def test_validate_rejects_expired_intermediate() -> None:
    root_key, inter_key, leaf_key = _key(), _key(), _key()
    inter = _cert(
        'Intermediate',
        inter_key,
        'Root CA',
        root_key,
        ca=True,
        not_before=NOW - datetime.timedelta(days=100),
        not_after=NOW - datetime.timedelta(days=1),
    )
    leaf = _cert('leaf.example.com', leaf_key, 'Intermediate', inter_key)

    with pytest.raises(web_certificate.CertificateError, match='Chain'):
        web_certificate.validate(_pem(leaf), _key_pem(leaf_key), _pem(inter))


def test_validate_rejects_passphrase_protected_key() -> None:
    key = _key()
    certificate = _pem(_cert('a.example.com', key))

    with pytest.raises(web_certificate.CertificateError, match='passphrase'):
        web_certificate.validate(certificate, _key_pem(key, b'secret'))


@pytest.mark.parametrize(
    ('cert_pem', 'key_pem', 'message'),
    [
        (b'not a certificate', b'', 'PEM format'),
        (
            b'-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n',
            b'',
            'readable PEM certificate',
        ),
        (b'-----BEGIN' + b'A' * (64 * 1024), b'', 'too large'),
    ],
)
def test_validate_rejects_garbage(
    cert_pem: bytes, key_pem: bytes, message: str
) -> None:
    with pytest.raises(web_certificate.CertificateError, match=message):
        web_certificate.validate(cert_pem, key_pem)


def test_validate_rejects_unreadable_key() -> None:
    cert_pem, _ = _self_signed()

    with pytest.raises(web_certificate.CertificateError, match='readable'):
        web_certificate.validate(cert_pem, b'-----BEGIN PRIVATE KEY-----\n')


def test_validate_accepts_rsa_and_lists_ip_sans() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    import ipaddress

    certificate = _cert(
        'screen',
        key,
        sans=[
            x509.DNSName('screen.lan'),
            x509.IPAddress(ipaddress.ip_address('192.0.2.10')),
        ],
    )

    result = web_certificate.validate(_pem(certificate), _key_pem(key))

    assert result.info.sans == ['screen.lan', '192.0.2.10']


@pytest.mark.parametrize(
    'domain', ['192.0.2.1', 'localhost', '-bad.example.com', 'a b.com']
)
def test_validate_domain_rejects(domain: str) -> None:
    with pytest.raises(web_certificate.CertificateError):
        web_certificate.validate_domain(domain)


def test_validate_domain_normalizes() -> None:
    assert (
        web_certificate.validate_domain(' Screen.Example.COM. ')
        == 'screen.example.com'
    )


def test_validate_email() -> None:
    assert web_certificate.validate_email('') == ''
    assert web_certificate.validate_email('ops@example.com') == (
        'ops@example.com'
    )
    with pytest.raises(web_certificate.CertificateError):
        web_certificate.validate_email('ops@example.com"}')


# --------------------------------------------------------------------
# State
# --------------------------------------------------------------------


def test_default_state_without_files() -> None:
    assert web_certificate.load_state() == web_certificate.WebTlsState()


def test_install_custom_then_revert() -> None:
    cert_pem, key_pem = _self_signed()
    web_certificate.install_custom(web_certificate.validate(cert_pem, key_pem))

    assert web_certificate.load_state().mode == web_certificate.MODE_CUSTOM
    assert stat.S_IMODE(web_certificate.key_path().stat().st_mode) == 0o600
    info = web_certificate.current_custom_info()
    assert info is not None and info.subject == 'CN=screen.example.com'

    web_certificate.revert_to_default()

    assert web_certificate.load_state().mode == web_certificate.MODE_DEFAULT
    assert not web_certificate.cert_path().exists()
    assert web_certificate.current_custom_info() is None


def test_custom_state_with_missing_files_falls_back_to_default() -> None:
    web_certificate.save_state(
        web_certificate.WebTlsState(mode=web_certificate.MODE_CUSTOM)
    )

    assert web_certificate.load_state().mode == web_certificate.MODE_DEFAULT


@pytest.mark.parametrize(
    'raw', ['{not json', '{"mode": "bogus"}', '{"mode": "acme"}']
)
def test_bad_state_falls_back_to_default(raw: str) -> None:
    web_certificate.web_dir().mkdir(parents=True)
    web_certificate.state_path().write_text(raw)

    assert web_certificate.load_state().mode == web_certificate.MODE_DEFAULT


def test_set_acme_round_trips() -> None:
    web_certificate.set_acme('Screen.Example.com', 'ops@example.com', True)

    state = web_certificate.load_state()

    assert state == web_certificate.WebTlsState(
        mode='acme',
        domain='screen.example.com',
        email='ops@example.com',
        staging=True,
    )


def test_snapshot_restore_round_trip() -> None:
    saved = web_certificate.snapshot()
    cert_pem, key_pem = _self_signed()
    web_certificate.install_custom(web_certificate.validate(cert_pem, key_pem))

    web_certificate.restore(saved)

    assert web_certificate.load_state().mode == web_certificate.MODE_DEFAULT
    assert not web_certificate.state_path().exists()
    assert not web_certificate.key_path().exists()


# --------------------------------------------------------------------
# Migration from the old enable_ssl.sh layout
# --------------------------------------------------------------------


def _anthias_dir() -> Path:
    return node_identity.node_dir().parent.parent


def _legacy_ssl() -> Path:
    path = node_identity.node_dir().parent
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_migrate_byo_certificate() -> None:
    cert_pem, key_pem = _self_signed()
    (_legacy_ssl() / 'cert.pem').write_bytes(cert_pem)
    (_legacy_ssl() / 'key.pem').write_bytes(key_pem)
    (_anthias_dir() / 'Caddyfile').write_text(
        ':443 {\n    tls /etc/anthias/ssl/cert.pem /etc/anthias/ssl/key.pem\n}'
    )

    outcome = web_certificate.migrate_legacy_layout()

    assert outcome == 'migrated enable_ssl.sh certificate'
    assert web_certificate.load_state().mode == web_certificate.MODE_CUSTOM
    assert not (_legacy_ssl() / 'key.pem').exists()
    assert (_legacy_ssl() / 'key.pem.migrated').exists()
    assert (_anthias_dir() / 'Caddyfile.migrated').exists()
    assert web_certificate.migrate_legacy_layout() is None


def test_migrate_expired_byo_certificate_falls_back() -> None:
    key = _key()
    expired = _cert(
        'old.example.com',
        key,
        not_before=NOW - datetime.timedelta(days=100),
        not_after=NOW - datetime.timedelta(days=1),
    )
    (_legacy_ssl() / 'cert.pem').write_bytes(_pem(expired))
    (_legacy_ssl() / 'key.pem').write_bytes(_key_pem(key))

    outcome = web_certificate.migrate_legacy_layout()

    assert outcome is not None and 'rejected' in outcome
    assert web_certificate.load_state().mode == web_certificate.MODE_DEFAULT


def test_migrate_domain_setup() -> None:
    _anthias_dir().mkdir(parents=True, exist_ok=True)
    (_anthias_dir() / 'Caddyfile').write_text(
        '{\n    admin off\n    email ops@example.com\n'
        '    acme_ca https://acme-staging-v02.api.letsencrypt.org/directory\n'
        '}\n\nscreen.example.com {\n    reverse_proxy anthias-server:8080\n}\n'
    )

    web_certificate.migrate_legacy_layout()

    assert web_certificate.load_state() == web_certificate.WebTlsState(
        mode='acme',
        domain='screen.example.com',
        email='ops@example.com',
        staging=True,
    )


def test_migrate_local_ca_setup_uses_node_certificate() -> None:
    _anthias_dir().mkdir(parents=True, exist_ok=True)
    (_anthias_dir() / 'Caddyfile').write_text(
        '{\n    admin off\n}\n\n:80 {\n    redir https://{host}{uri}\n}\n\n'
        ':443 {\n    tls internal {\n        on_demand\n    }\n}\n'
    )

    outcome = web_certificate.migrate_legacy_layout()

    assert outcome is not None and 'local-CA' in outcome
    assert web_certificate.load_state().mode == web_certificate.MODE_DEFAULT


def test_migrate_nothing_to_do() -> None:
    assert web_certificate.migrate_legacy_layout() is None


# --------------------------------------------------------------------
# Caddy config
# --------------------------------------------------------------------


def _servers(config: dict[str, Any]) -> dict[str, Any]:
    servers: dict[str, Any] = config['apps']['http']['servers']
    return servers


def test_default_config_serves_node_certificate_everywhere() -> None:
    config = caddy.build_config(web_certificate.WebTlsState())

    load_files = config['apps']['tls']['certificates']['load_files']
    web = _servers(config)['anthias_web']

    assert load_files == [
        {
            'certificate': str(caddy.certs_dir() / 'node.crt'),
            'key': str(caddy.certs_dir() / 'node.key'),
            'tags': [caddy.TAG_NODE],
        }
    ]
    assert web['listen'] == [':443']
    assert web['tls_connection_policies'] == [
        {'certificate_selection': {'any_tag': [caddy.TAG_NODE]}}
    ]
    assert web['automatic_https'] == {'disable': True}
    assert config['admin']['listen'] == (f'unix/{caddy.admin_socket()}|0600')


def test_custom_config_tags_web_certificate_by_fingerprint() -> None:
    cert_pem, key_pem = _self_signed()
    validated = web_certificate.validate(cert_pem, key_pem)
    web_certificate.install_custom(validated)

    config = caddy.build_config()

    load_files = config['apps']['tls']['certificates']['load_files']
    tag = f'{caddy.TAG_WEB}-{validated.info.fingerprint[:16]}'
    assert [f['tags'] for f in load_files] == [[caddy.TAG_NODE], [tag]]
    assert _servers(config)['anthias_web']['tls_connection_policies'] == [
        {'certificate_selection': {'any_tag': [tag]}}
    ]


def test_acme_config() -> None:
    state = web_certificate.WebTlsState(
        mode='acme',
        domain='screen.example.com',
        email='ops@example.com',
        staging=True,
    )

    config = caddy.build_config(state)

    tls = config['apps']['tls']
    assert tls['certificates']['automate'] == ['screen.example.com']
    assert tls['automation']['policies'] == [
        {
            'subjects': ['screen.example.com'],
            'issuers': [
                {
                    'module': 'acme',
                    'email': 'ops@example.com',
                    'ca': 'https://acme-staging-v02.api.letsencrypt.org'
                    '/directory',
                }
            ],
        }
    ]
    web = _servers(config)['anthias_web']
    assert web['tls_connection_policies'][0] == {
        'match': {'sni': ['screen.example.com']}
    }
    assert web['automatic_https'] == {'disable_redirects': True}


def test_http_server_keeps_api_and_redirects_ui() -> None:
    http = _servers(caddy.build_config())['anthias_http']

    api, redirect = http['routes']
    assert http['listen'] == [':80']
    assert api['match'] == [{'path': ['/api/*']}]
    assert api['handle'] == [caddy.proxy_handler()]
    assert redirect['handle'][0]['status_code'] == 308
    assert redirect['handle'][0]['headers']['Location'] == [
        'https://{http.request.host}{http.request.uri}'
    ]


def test_ui_proxies_strip_the_listener_header() -> None:
    handler = caddy.proxy_handler()

    assert handler['upstreams'] == [{'dial': 'anthias-server:8080'}]
    assert handler['headers']['request']['delete'] == ['X-Anthias-Listener']


def test_edge_limits_leave_bodies_unbounded() -> None:
    for server in _servers(caddy.build_config()).values():
        assert server['read_header_timeout'] == '10s'
        assert server['idle_timeout'] == '30s'
        assert 'read_timeout' not in server
        assert 'write_timeout' not in server
    assert 'request_body' not in json.dumps(caddy.build_config())


def test_server_providers_are_merged_and_isolated() -> None:
    def m2m() -> dict[str, dict[str, Any]]:
        return {'anthias_m2m': {'listen': [':8443']}}

    def broken() -> dict[str, dict[str, Any]]:
        raise RuntimeError('boom')

    caddy.register_server_provider(m2m)
    caddy.register_server_provider(m2m)
    caddy.register_server_provider(broken)
    try:
        servers = _servers(caddy.build_config())
    finally:
        caddy.unregister_server_provider(m2m)
        caddy.unregister_server_provider(broken)

    assert servers['anthias_m2m'] == {'listen': [':8443']}
    assert set(servers) == {'anthias_http', 'anthias_web', 'anthias_m2m'}
    assert 'anthias_m2m' not in _servers(caddy.build_config())


def test_bootstrap_config_matches_the_generated_admin_listener() -> None:
    bootstrap = json.loads(
        (
            Path(__file__).resolve().parent.parent
            / 'docker'
            / 'caddy'
            / 'bootstrap.json'
        ).read_text()
    )

    assert bootstrap['admin']['listen'] == (
        'unix//run/anthias-caddy/admin.sock|0600'
    )
    assert bootstrap['admin']['config'] == {'persist': True}


def test_stage_certificates() -> None:
    cert_pem, key_pem = _self_signed()
    web_certificate.install_custom(web_certificate.validate(cert_pem, key_pem))

    caddy.stage_certificates(web_certificate.load_state())

    assert (
        (caddy.certs_dir() / 'web.crt')
        .read_bytes()
        .startswith(b'-----BEGIN CERTIFICATE-----')
    )
    assert stat.S_IMODE((caddy.certs_dir() / 'node.key').stat().st_mode) == (
        0o600
    )

    web_certificate.revert_to_default()
    caddy.stage_certificates(web_certificate.load_state())

    assert not (caddy.certs_dir() / 'web.crt').exists()
    assert (caddy.certs_dir() / 'node.crt').exists()


# --------------------------------------------------------------------
# Admin API over the Unix socket
# --------------------------------------------------------------------


class _FakeAdmin(ThreadingMixIn, UnixStreamServer):
    daemon_threads = True
    loads: list[dict[str, Any]]
    reject: bool


class _Handler(BaseHTTPRequestHandler):
    server: _FakeAdmin

    def address_string(self) -> str:
        return 'unix'

    def log_message(self, *_args: Any) -> None:
        pass

    def _reply(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._reply(200, b'{}')

    def do_POST(self) -> None:
        length = int(self.headers['Content-Length'])
        config = json.loads(self.rfile.read(length))
        if self.server.reject:
            self._reply(400, b'{"error": "loading config: bad cert"}')
            return
        self.server.loads.append(config)
        self._reply(200, b'')


@pytest.fixture
def fake_admin() -> Iterator[_FakeAdmin]:
    caddy.RUN_DIR.mkdir(parents=True, exist_ok=True)
    server = _FakeAdmin(str(caddy.admin_socket()), _Handler)
    server.loads = []
    server.reject = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        os.unlink(caddy.admin_socket())


def test_apply_loads_config_through_the_socket(
    fake_admin: _FakeAdmin,
) -> None:
    assert caddy.is_reachable()

    result = caddy.apply()

    assert result.mode == web_certificate.MODE_DEFAULT
    assert result.servers == ('anthias_http', 'anthias_web')
    assert fake_admin.loads == [caddy.build_config()]
    assert (caddy.certs_dir() / 'node.crt').exists()


def test_apply_reports_rejection(fake_admin: _FakeAdmin) -> None:
    fake_admin.reject = True

    with pytest.raises(caddy.CaddyConfigError, match='bad cert'):
        caddy.apply()


def test_apply_change_rolls_back_a_rejected_certificate(
    fake_admin: _FakeAdmin,
) -> None:
    fake_admin.reject = True
    cert_pem, key_pem = _self_signed()
    validated = web_certificate.validate(cert_pem, key_pem)

    with pytest.raises(caddy.CaddyConfigError):
        caddy.apply_change(lambda: web_certificate.install_custom(validated))

    assert web_certificate.load_state().mode == web_certificate.MODE_DEFAULT
    assert not web_certificate.cert_path().exists()


def test_apply_change_keeps_an_accepted_certificate(
    fake_admin: _FakeAdmin,
) -> None:
    cert_pem, key_pem = _self_signed()
    validated = web_certificate.validate(cert_pem, key_pem)

    result = caddy.apply_change(
        lambda: web_certificate.install_custom(validated)
    )

    assert result.mode == web_certificate.MODE_CUSTOM
    assert len(fake_admin.loads) == 1


def test_unreachable_caddy_raises_and_rolls_back() -> None:
    cert_pem, key_pem = _self_signed()
    validated = web_certificate.validate(cert_pem, key_pem)

    assert not caddy.is_reachable()
    with pytest.raises(caddy.CaddyError, match='Cannot reach Caddy'):
        caddy.apply_change(lambda: web_certificate.install_custom(validated))
    assert web_certificate.load_state().mode == web_certificate.MODE_DEFAULT


def test_apply_on_startup_retries_until_caddy_answers() -> None:
    calls: list[str] = []

    def fake_apply() -> caddy.ApplyResult:
        calls.append('apply')
        if len(calls) < 3:
            raise caddy.CaddyError('not yet')
        return caddy.ApplyResult(mode='default', servers=())

    with mock.patch.object(caddy, 'apply', side_effect=fake_apply):
        assert caddy.apply_on_startup(attempts=5, sleep=lambda _s: None)
    assert len(calls) == 3


def test_apply_on_startup_gives_up() -> None:
    with mock.patch.object(
        caddy, 'apply', side_effect=caddy.CaddyError('down')
    ) as apply:
        assert not caddy.apply_on_startup(attempts=3, sleep=lambda _s: None)
    assert apply.call_count == 3


def test_apply_on_startup_does_not_retry_a_rejection() -> None:
    with mock.patch.object(
        caddy, 'apply', side_effect=caddy.CaddyConfigError('bad')
    ) as apply:
        assert not caddy.apply_on_startup(attempts=3, sleep=lambda _s: None)
    assert apply.call_count == 1


def test_apply_on_startup_migrates_first() -> None:
    order: list[str] = []
    with (
        mock.patch.object(
            web_certificate,
            'migrate_legacy_layout',
            side_effect=lambda: order.append('migrate'),
        ),
        mock.patch.object(
            caddy,
            'apply',
            side_effect=lambda: order.append('apply'),
        ),
    ):
        caddy.apply_on_startup(attempts=1)
    assert order == ['migrate', 'apply']


def test_background_apply_needs_the_run_directory() -> None:
    assert not caddy.RUN_DIR.exists()
    assert caddy.start_background_apply() is None


def test_background_apply_starts_a_daemon_thread() -> None:
    caddy.RUN_DIR.mkdir(parents=True)
    with mock.patch.object(caddy, 'apply_on_startup') as startup:
        thread = caddy.start_background_apply()
        assert thread is not None
        thread.join(5)
    assert thread.daemon
    startup.assert_called_once_with()


def test_unix_connection_closes_socket_on_failure(tmp_path: Path) -> None:
    conn = caddy._UnixHTTPConnection(tmp_path / 'missing.sock', 1.0)
    with pytest.raises(OSError):
        conn.connect()
    assert conn.sock is None or conn.sock.fileno() == -1


# --------------------------------------------------------------------
# Settings page
# --------------------------------------------------------------------


def _upload(name: str, data: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type='application/x-pem')


@pytest.mark.django_db
def test_settings_shows_https_section() -> None:
    response = Client().get(reverse('anthias_app:settings'))
    body = response.content.decode()

    assert response.status_code == 200
    assert 'HTTPS certificate' in body
    fingerprint = node_identity.fingerprint().upper()
    assert (
        ':'.join(fingerprint[i : i + 2] for i in range(0, len(fingerprint), 2))
        in body
    )
    assert 'Revert to default' not in body


@pytest.mark.django_db
def test_settings_upload_installs_and_applies() -> None:
    cert_pem, key_pem = _self_signed()
    with mock.patch.object(caddy, 'apply') as apply:
        response = Client().post(
            reverse('anthias_app:settings_certificate_upload'),
            {
                'certificate': _upload('cert.pem', cert_pem),
                'private_key': _upload('key.pem', key_pem),
            },
            follow=True,
        )

    apply.assert_called_once_with()
    assert web_certificate.load_state().mode == web_certificate.MODE_CUSTOM
    body = response.content.decode()
    assert 'screen.example.com' in body
    assert 'Revert to default' in body


@pytest.mark.django_db
def test_settings_upload_rejects_mismatched_key() -> None:
    cert_pem, _ = _self_signed()
    _, other_key = _self_signed()
    with mock.patch.object(caddy, 'apply') as apply:
        response = Client().post(
            reverse('anthias_app:settings_certificate_upload'),
            {
                'certificate': _upload('cert.pem', cert_pem),
                'private_key': _upload('key.pem', other_key),
            },
            follow=True,
        )

    apply.assert_not_called()
    assert web_certificate.load_state().mode == web_certificate.MODE_DEFAULT
    assert 'does not belong' in response.content.decode()


@pytest.mark.django_db
def test_settings_upload_requires_both_files() -> None:
    cert_pem, _ = _self_signed()
    response = Client().post(
        reverse('anthias_app:settings_certificate_upload'),
        {'certificate': _upload('cert.pem', cert_pem)},
        follow=True,
    )

    assert 'Choose both' in response.content.decode()


@pytest.mark.django_db
def test_settings_upload_rejects_oversized_file() -> None:
    response = Client().post(
        reverse('anthias_app:settings_certificate_upload'),
        {
            'certificate': _upload('cert.pem', b'A' * (65 * 1024)),
            'private_key': _upload('key.pem', b'x'),
        },
        follow=True,
    )

    assert 'too large' in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('error', 'message'),
    [
        (caddy.CaddyConfigError('bad'), 'rejected this certificate'),
        (caddy.CaddyError('down'), 'not reachable'),
    ],
)
def test_settings_upload_reports_caddy_failures(
    error: Exception, message: str
) -> None:
    cert_pem, key_pem = _self_signed()
    with mock.patch.object(caddy, 'apply', side_effect=error):
        response = Client().post(
            reverse('anthias_app:settings_certificate_upload'),
            {
                'certificate': _upload('cert.pem', cert_pem),
                'private_key': _upload('key.pem', key_pem),
            },
            follow=True,
        )

    assert message in response.content.decode()
    assert web_certificate.load_state().mode == web_certificate.MODE_DEFAULT


@pytest.mark.django_db
def test_settings_revert() -> None:
    cert_pem, key_pem = _self_signed()
    web_certificate.install_custom(web_certificate.validate(cert_pem, key_pem))
    with mock.patch.object(caddy, 'apply') as apply:
        response = Client().post(
            reverse('anthias_app:settings_certificate_revert'), follow=True
        )

    apply.assert_called_once_with()
    assert web_certificate.load_state().mode == web_certificate.MODE_DEFAULT
    assert 'Switched back' in response.content.decode()


@pytest.mark.django_db
def test_settings_revert_failure_keeps_custom() -> None:
    cert_pem, key_pem = _self_signed()
    web_certificate.install_custom(web_certificate.validate(cert_pem, key_pem))
    with mock.patch.object(
        caddy, 'apply', side_effect=caddy.CaddyError('down')
    ):
        response = Client().post(
            reverse('anthias_app:settings_certificate_revert'), follow=True
        )

    assert 'nothing was changed' in response.content.decode()
    assert web_certificate.load_state().mode == web_certificate.MODE_CUSTOM


@pytest.mark.django_db
def test_settings_upload_needs_auth_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_server.settings import settings

    monkeypatch.setitem(settings.data, 'auth_backend', 'auth_basic')
    response = Client().post(
        reverse('anthias_app:settings_certificate_upload'), {}
    )

    assert response.status_code in (302, 401, 403)
    assert web_certificate.load_state().mode == web_certificate.MODE_DEFAULT


# --------------------------------------------------------------------
# manage.py web_tls (what bin/enable_ssl.sh runs)
# --------------------------------------------------------------------


def test_web_tls_custom_default_and_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cert_pem, key_pem = _self_signed()
    (tmp_path / 'c.pem').write_bytes(cert_pem)
    (tmp_path / 'k.pem').write_bytes(key_pem)

    with mock.patch.object(caddy, 'apply') as apply:
        apply.return_value = caddy.ApplyResult('custom', ())
        call_command(
            'web_tls',
            'custom',
            '--cert',
            str(tmp_path / 'c.pem'),
            '--key',
            str(tmp_path / 'k.pem'),
        )
        assert web_certificate.load_state().mode == 'custom'

        call_command('web_tls', 'status')
        out = capsys.readouterr().out
        assert 'CN=screen.example.com' in out
        assert node_identity.fingerprint() in out

        apply.return_value = caddy.ApplyResult('default', ())
        call_command('web_tls', 'default')
        assert web_certificate.load_state().mode == 'default'


def test_web_tls_acme(capsys: pytest.CaptureFixture[str]) -> None:
    with mock.patch.object(
        caddy, 'apply', return_value=caddy.ApplyResult('acme', ())
    ):
        call_command(
            'web_tls', 'acme', '--domain', 'screen.example.com', '--staging'
        )

    assert web_certificate.load_state().domain == 'screen.example.com'
    call_command('web_tls', 'status')
    assert 'Domain: screen.example.com' in capsys.readouterr().out


def test_web_tls_rejects_bad_input(tmp_path: Path) -> None:
    cert_pem, _ = _self_signed()
    _, other_key = _self_signed()
    (tmp_path / 'c.pem').write_bytes(cert_pem)
    (tmp_path / 'k.pem').write_bytes(other_key)

    with pytest.raises(CommandError, match='does not belong'):
        call_command(
            'web_tls',
            'custom',
            '--cert',
            str(tmp_path / 'c.pem'),
            '--key',
            str(tmp_path / 'k.pem'),
        )
    with pytest.raises(CommandError, match='Cannot read'):
        call_command(
            'web_tls',
            'custom',
            '--cert',
            str(tmp_path / 'missing.pem'),
            '--key',
            str(tmp_path / 'k.pem'),
        )
    with pytest.raises(CommandError, match='IP address'):
        call_command('web_tls', 'acme', '--domain', '192.0.2.1')


@pytest.mark.parametrize(
    ('error', 'message'),
    [
        (caddy.CaddyConfigError('nope'), 'Caddy rejected'),
        (caddy.CaddyError('Cannot reach Caddy'), 'Cannot reach'),
    ],
)
def test_web_tls_apply_failures(error: Exception, message: str) -> None:
    with (
        mock.patch.object(caddy, 'apply', side_effect=error),
        pytest.raises(CommandError, match=message),
    ):
        call_command('web_tls', 'apply')
