"""Pinned HTTPS against a real local TLS server with a self-signed cert."""

from __future__ import annotations

import hashlib
import http.server
import shutil
import ssl
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest
import requests

from anthias_server.lib.pinned_http import (
    PinnedFingerprintError,
    normalize_fingerprint,
    pinned_session,
)

pytestmark = pytest.mark.skipif(
    shutil.which('openssl') is None, reason='openssl CLI not available'
)


def _make_cert(directory: Path, name: str) -> tuple[Path, Path, str]:
    cert = directory / f'{name}.crt'
    key = directory / f'{name}.key'
    subprocess.run(
        [
            'openssl',
            'req',
            '-x509',
            '-newkey',
            'ec',
            '-pkeyopt',
            'ec_paramgen_curve:prime256v1',
            '-nodes',
            '-days',
            '2',
            '-subj',
            f'/CN={name}',
            '-keyout',
            str(key),
            '-out',
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    return cert, key, hashlib.sha256(der).hexdigest()


class _Handler(http.server.BaseHTTPRequestHandler):
    seen_auth: ClassVar[list[str | None]] = []

    def do_GET(self) -> None:
        _Handler.seen_auth.append(self.headers.get('Authorization'))
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return None


@pytest.fixture
def tls_server(tmp_path: Path) -> Iterator[tuple[str, str, str]]:
    cert, key, fingerprint = _make_cert(tmp_path, 'node')
    _, _, other_fp = _make_cert(tmp_path, 'other')
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _Handler.seen_auth = []
    try:
        url = f'https://127.0.0.1:{server.server_address[1]}/api/v2/info'
        yield url, fingerprint, other_fp
    finally:
        server.shutdown()
        server.server_close()


def test_correct_pin_connects(tls_server: tuple[str, str, str]) -> None:
    url, fingerprint, _ = tls_server
    with pinned_session(fingerprint) as session:
        response = session.get(url, timeout=5)
    assert response.status_code == 200
    assert response.json() == {'ok': True}


def test_colon_uppercase_pin_connects(
    tls_server: tuple[str, str, str],
) -> None:
    url, fingerprint, _ = tls_server
    pin = ':'.join(fingerprint[i : i + 2] for i in range(0, 64, 2)).upper()
    with pinned_session(pin) as session:
        assert session.get(url, timeout=5).status_code == 200


def test_wrong_pin_is_refused_before_request_is_sent(
    tls_server: tuple[str, str, str],
) -> None:
    url, _, other_fp = tls_server
    with (
        pinned_session(other_fp) as session,
        pytest.raises(requests.exceptions.SSLError),
    ):
        session.get(url, headers={'Authorization': 'Bearer secret'}, timeout=5)
    assert _Handler.seen_auth == []


def test_plain_http_is_refused() -> None:
    with (
        pinned_session('ab' * 32) as session,
        pytest.raises(requests.exceptions.InvalidSchema),
    ):
        session.get('http://127.0.0.1:9/', timeout=1)


def test_proxy_environment_is_ignored(
    tls_server: tuple[str, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:9')
    url, fingerprint, _ = tls_server
    with pinned_session(fingerprint) as session:
        assert session.get(url, timeout=5).status_code == 200


@pytest.mark.parametrize('value', ['', 'abc', 'zz' * 32, 'ab' * 31])
def test_normalize_fingerprint_rejects(value: str) -> None:
    with pytest.raises(PinnedFingerprintError):
        normalize_fingerprint(value)
