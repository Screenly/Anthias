"""Controller transport: the pinned HTTPS client and the token vault.

The pinning tests run a real TLS server on localhost with a freshly
generated self-signed certificate, so they exercise the same urllib3
handshake path the controller uses against a screen.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import ssl
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from anthias_server.controller import vault
from anthias_server.controller.client import (
    NodeCertificateError,
    NodeClient,
    NodeOfflineError,
    NodeRequestError,
    NodeUnauthorizedError,
    normalize_fingerprint,
)


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
    cert_path = tmp / f'{name}.crt'
    key_path = tmp / f'{name}.key'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    fingerprint = hashlib.sha256(
        cert.public_bytes(serialization.Encoding.DER)
    ).hexdigest()
    return str(cert_path), str(key_path), fingerprint


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        self.body: Any = {'anthias_version': 'test', 'device_model': 'Pi'}


def _serve(cert: str, key: str, recorder: _Recorder) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _reply(self) -> None:
            length = int(self.headers.get('Content-Length') or 0)
            body = self.rfile.read(length) if length else b''
            recorder.requests.append(
                {
                    'method': self.command,
                    'path': self.path,
                    'auth': self.headers.get('Authorization'),
                    'range': self.headers.get('Content-Range'),
                    'upload_id': self.headers.get('X-Upload-Id'),
                    'body_len': len(body),
                }
            )
            payload = json.dumps(recorder.body).encode()
            self.send_response(recorder.status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_DELETE = do_PATCH = _reply

        def log_message(self, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def screen(tmp_path: Path) -> Iterator[tuple[str, str, _Recorder]]:
    cert, key, fingerprint = _make_cert(tmp_path, 'screen')
    recorder = _Recorder()
    server = _serve(cert, key, recorder)
    url = f'https://127.0.0.1:{server.server_address[1]}'
    try:
        yield url, fingerprint, recorder
    finally:
        server.shutdown()
        server.server_close()


def test_pinned_client_talks_to_the_pinned_cert(
    screen: tuple[str, str, _Recorder],
) -> None:
    url, fingerprint, recorder = screen
    with NodeClient(url, fingerprint, 'anthias_tok') as client:
        assert client.info()['anthias_version'] == 'test'
    assert recorder.requests[0]['path'] == '/api/v2/info'
    assert recorder.requests[0]['auth'] == 'Bearer anthias_tok'


def test_pin_accepts_colon_and_uppercase_forms(
    screen: tuple[str, str, _Recorder],
) -> None:
    url, fingerprint, _ = screen
    pretty = ':'.join(fingerprint[i : i + 2] for i in range(0, 64, 2)).upper()
    assert normalize_fingerprint(pretty) == fingerprint
    with NodeClient(url, pretty) as client:
        client.info()


def test_wrong_pin_is_refused_before_the_token_is_sent(
    screen: tuple[str, str, _Recorder],
) -> None:
    url, fingerprint, recorder = screen
    wrong = hashlib.sha256(b'someone else').hexdigest()
    with (
        NodeClient(url, wrong, 'anthias_secret') as client,
        pytest.raises(NodeCertificateError),
    ):
        client.info()
    # The handshake failed, so the server never saw a request.
    assert recorder.requests == []
    assert fingerprint != wrong


def test_proxy_environment_is_ignored(
    screen: tuple[str, str, _Recorder], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, fingerprint, _ = screen
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:9')
    with NodeClient(url, fingerprint) as client:
        client.info()


def test_offline_screen_raises_offline() -> None:
    with (
        NodeClient('https://127.0.0.1:9', 'a' * 64) as client,
        pytest.raises(NodeOfflineError) as excinfo,
    ):
        client.info()
    # One readable line, not urllib3's nested repr.
    assert str(excinfo.value) == (
        'Cannot reach 127.0.0.1:9: Connection refused.'
    )


def test_plain_http_is_refused() -> None:
    with (
        NodeClient('http://127.0.0.1:9', 'a' * 64) as client,
        pytest.raises(NodeOfflineError),
    ):
        client.info()


def test_revoked_token_raises_unauthorized(
    screen: tuple[str, str, _Recorder],
) -> None:
    url, fingerprint, recorder = screen
    recorder.status = 401
    recorder.body = {'detail': 'Invalid or revoked token.'}
    with (
        NodeClient(url, fingerprint, 'anthias_x') as client,
        pytest.raises(NodeUnauthorizedError),
    ):
        client.info()


def test_error_status_carries_detail(
    screen: tuple[str, str, _Recorder],
) -> None:
    url, fingerprint, recorder = screen
    recorder.status = 400
    recorder.body = {'uri': ['bad']}
    with (
        NodeClient(url, fingerprint, 'anthias_x') as client,
        pytest.raises(NodeRequestError) as excinfo,
    ):
        client.create_asset({'name': 'x'})
    assert excinfo.value.status_code == 400


def test_upload_is_sent_in_content_range_chunks(
    screen: tuple[str, str, _Recorder],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_server.controller import client as client_mod

    url, fingerprint, recorder = screen
    monkeypatch.setattr(client_mod, 'UPLOAD_CHUNK_BYTES', 1000)
    recorder.body = {'uri': '/data/x.tmp', 'ext': '.png', 'upload_id': 'abc'}
    media = tmp_path / 'a.png'
    media.write_bytes(os.urandom(2500))
    with NodeClient(url, fingerprint, 'anthias_x') as client:
        result = client.upload_file(str(media), 'a.png')
    assert result['uri'] == '/data/x.tmp'
    ranges = [r['range'] for r in recorder.requests]
    assert ranges == [
        'bytes 0-999/2500',
        'bytes 1000-1999/2500',
        'bytes 2000-2499/2500',
    ]
    assert [r['upload_id'] for r in recorder.requests] == [None, 'abc', 'abc']


# ---------------------------------------------------------------------------
# vault


@pytest.fixture
def key_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    path = tmp_path / 'controller.key'
    monkeypatch.setattr(vault, 'key_path', lambda: str(path))
    vault.reset_cache()
    yield path
    vault.reset_cache()


def test_vault_round_trip_and_key_file_mode(key_file: Path) -> None:
    ciphertext = vault.encrypt('anthias_secret_token')
    assert 'anthias_secret_token' not in ciphertext
    assert vault.decrypt(ciphertext) == 'anthias_secret_token'
    assert key_file.stat().st_mode & 0o777 == 0o600


def test_vault_with_another_key_cannot_decrypt(key_file: Path) -> None:
    ciphertext = vault.encrypt('anthias_secret_token')
    key_file.unlink()
    vault.reset_cache()
    with pytest.raises(vault.VaultError):
        vault.decrypt(ciphertext)


def test_key_file_is_not_in_backups() -> None:
    from anthias_server.lib import backup_helper

    assert vault.KEY_FILENAME not in backup_helper._CONFIG_FILES
