"""v2 API client for a managed screen, pinned to its certificate.

Screens serve their management API on :8443 with a long-lived
self-signed node certificate. The session from
:mod:`anthias_server.lib.pinned_http` accepts only the certificate
whose SHA-256 was recorded when the screen joined, and checks it during
the TLS handshake, before the bearer token leaves this device.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from mimetypes import guess_type
from typing import Any, Self
from urllib.parse import urlsplit

import requests

from anthias_server.lib import pinned_http

API_PREFIX = '/api/v2'
CONNECT_TIMEOUT_S = 5
READ_TIMEOUT_S = 60
UPLOAD_READ_TIMEOUT_S = 300
# Uploads are sent as Content-Range chunks so neither side holds a
# whole video in memory. 8 MiB keeps each request short on a Pi 3
# (about 3 s at the ~2.8 MB/s the spike measured).
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024
# Nothing a screen legitimately returns comes close: the largest body is
# the asset list. A bigger response is refused rather than buffered.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
ERROR_MESSAGE_MAX = 200
_monotonic = time.monotonic


class NodeError(Exception):
    """A screen could not be reached or refused a request."""

    status = 'offline'


class NodeOfflineError(NodeError):
    status = 'offline'


class NodeCertificateError(NodeError):
    """The screen presented a certificate other than the pinned one."""

    status = 'wrong_cert'


class NodeUnauthorizedError(NodeError):
    """The screen no longer accepts our token (revoked, or unpaired)."""

    status = 'unauthorized'


class NodeRequestError(NodeError):
    """The screen answered, but with an error."""

    status = 'online'

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


def normalize_fingerprint(value: str) -> str:
    """Lowercase hex without separators; ``''`` if not a SHA-256."""
    try:
        return pinned_http.normalize_fingerprint(value)
    except pinned_http.PinnedFingerprintError:
        return ''


_ERRNO_RE = re.compile(r'\[Errno -?\d+\] ([^\'"\)\]]+)')


def _offline_reason(url: str, exc: Exception) -> str:
    """A one-line reason instead of urllib3's nested repr."""
    where = urlsplit(url).netloc
    if isinstance(exc, (ResponseTooLargeError, ResponseDeadlineError)):
        return f'{where}: {exc}'
    if isinstance(exc, requests.exceptions.Timeout):
        return f'{where} did not answer in time.'
    if isinstance(exc, requests.exceptions.InvalidSchema):
        return 'Plain HTTP is never used for screens.'
    match = _ERRNO_RE.search(str(exc))
    reason = match.group(1).strip() if match else 'connection failed'
    message = f'Cannot reach {where}: {reason}.'
    if reason.lower() == 'connection refused':
        # The device answered but nothing listens on the management
        # port: a screen closes it when it leaves management (or when
        # authentication is turned off).
        message += (
            ' The device is up but not accepting management. It may have '
            'left management; if so, remove it here or join it again.'
        )
    return message


class ResponseTooLargeError(requests.exceptions.ConnectionError):
    pass


class ResponseDeadlineError(requests.exceptions.Timeout):
    pass


def _read_bounded(response: requests.Response, *, deadline: float) -> None:
    """Read the body into ``response`` with a size cap and a deadline.

    ``requests`` applies the read timeout per socket read, so a screen
    that drips one byte at a time, or answers with gigabytes, would
    otherwise hold a worker (or the join request) for as long as it
    likes and fill the controller's memory.
    """
    declared = response.headers.get('Content-Length')
    try:
        if declared is not None and int(declared) > MAX_RESPONSE_BYTES:
            response.close()
            raise ResponseTooLargeError(
                'The screen sent a response too large.'
            )
    except ValueError:
        pass
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_content(64 * 1024):
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise ResponseTooLargeError(
                    'The screen sent a response too large.'
                )
            if _monotonic() > deadline:
                raise ResponseDeadlineError(
                    'The screen took too long to answer.'
                )
            chunks.append(chunk)
    finally:
        response.close()
    response._content = b''.join(chunks)


def _error_message(response: requests.Response) -> str:
    """A short, single-line reason from a screen's error body."""
    try:
        body = response.json()
    except ValueError:
        text = response.text or response.reason or ''
    else:
        text = str(body)
        if isinstance(body, dict):
            for key in ('detail', 'message', 'error'):
                if isinstance(body.get(key), str):
                    text = str(body[key])
                    break
    flat = ''.join(c if c.isprintable() else ' ' for c in text)
    return ' '.join(flat.split())[:ERROR_MESSAGE_MAX]


class NodeClient:
    def __init__(
        self,
        base_url: str,
        fingerprint: str,
        token: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip('/')
        if session is None:
            try:
                session = pinned_http.pinned_session(fingerprint)
            except pinned_http.PinnedFingerprintError as exc:
                raise NodeCertificateError(
                    'No valid certificate fingerprint is stored for this '
                    'screen.'
                ) from exc
        self.session = session
        self.token = token

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        timeout: tuple[float, float] = (CONNECT_TIMEOUT_S, READ_TIMEOUT_S),
        expected: tuple[int, ...] = (200, 201, 204),
        **kwargs: Any,
    ) -> requests.Response:
        headers = dict(kwargs.pop('headers', None) or {})
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        url = f'{self.base_url}{API_PREFIX}{path}'
        started = _monotonic()
        try:
            response = self.session.request(
                method,
                url,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
                stream=True,
                **kwargs,
            )
            _read_bounded(response, deadline=started + sum(timeout))
        except requests.exceptions.SSLError as exc:
            if 'fingerprint' in str(exc).lower():
                raise NodeCertificateError(
                    'The screen presented a different certificate than '
                    'the one it was paired with.'
                ) from exc
            raise NodeOfflineError(f'TLS error: {exc}') from exc
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.InvalidSchema,
        ) as exc:
            raise NodeOfflineError(_offline_reason(url, exc)) from exc
        if response.status_code in (401, 403) and self.token:
            raise NodeUnauthorizedError(
                "The screen did not accept this controller's token."
            )
        if response.status_code not in expected:
            raise NodeRequestError(
                f'{method} {path}: HTTP {response.status_code}: '
                f'{_error_message(response)}',
                response.status_code,
            )
        return response

    def json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.request(method, path, **kwargs)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise NodeRequestError(
                f'{method} {path}: response was not JSON.'
            ) from exc

    # --- read ---------------------------------------------------------

    def info(self) -> dict[str, Any]:
        data = self.json('GET', '/info')
        return data if isinstance(data, dict) else {}

    def pairing_status(self) -> dict[str, Any]:
        data = self.json('GET', '/pairing')
        return data if isinstance(data, dict) else {}

    def list_assets(self) -> list[dict[str, Any]]:
        data = self.json('GET', '/assets')
        if not isinstance(data, list):
            raise NodeRequestError('GET /assets: expected a list.')
        return [a for a in data if isinstance(a, dict)]

    # --- write --------------------------------------------------------

    def unpair(self) -> None:
        # Called from the web request that removes a screen, so keep
        # the wait for an unreachable screen short.
        self.request('DELETE', '/pairing', timeout=(CONNECT_TIMEOUT_S, 10))

    def create_asset(self, body: dict[str, Any]) -> dict[str, Any]:
        data = self.json('POST', '/assets', json=body)
        if not isinstance(data, dict) or 'asset_id' not in data:
            raise NodeRequestError('POST /assets: no asset_id returned.')
        return data

    def update_asset(
        self, asset_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        data = self.json('PATCH', f'/assets/{asset_id}', json=body)
        return data if isinstance(data, dict) else {}

    def delete_asset(self, asset_id: str) -> None:
        self.request('DELETE', f'/assets/{asset_id}', expected=(200, 204, 404))

    def set_order(self, asset_ids: list[str]) -> None:
        self.request(
            'POST', '/assets/order', json={'ids': ','.join(asset_ids)}
        )

    def reboot(self) -> None:
        self.request('POST', '/reboot')

    def display_power(self, state: str) -> str:
        data = self.json('POST', f'/display/{state}')
        if isinstance(data, dict) and isinstance(data.get('message'), str):
            return str(data['message'])
        return ''

    def playback(self, command: str) -> None:
        self.request('GET', f'/assets/control/{command}')

    def upload_file(self, file_path: str, filename: str) -> dict[str, Any]:
        """Upload a file in Content-Range chunks.

        Returns the screen's ``{uri, ext}`` for ``POST /assets``.
        """
        size = os.path.getsize(file_path)
        mimetype = guess_type(filename)[0] or 'application/octet-stream'
        if size == 0:
            raise NodeRequestError(f'{filename} is empty.')
        upload_id: str | None = None
        result: dict[str, Any] = {}
        for start, chunk in _chunks(file_path, size):
            end = start + len(chunk) - 1
            headers = {'Content-Range': f'bytes {start}-{end}/{size}'}
            if upload_id:
                headers['X-Upload-Id'] = upload_id
            data = self.json(
                'POST',
                '/file_asset',
                files={'file_upload': (filename, chunk, mimetype)},
                headers=headers,
                timeout=(CONNECT_TIMEOUT_S, UPLOAD_READ_TIMEOUT_S),
            )
            if not isinstance(data, dict) or 'uri' not in data:
                raise NodeRequestError('POST /file_asset: no uri returned.')
            result = data
            upload_id = str(data.get('upload_id') or upload_id or '')
        return result


def _chunks(file_path: str, size: int) -> Iterator[tuple[int, bytes]]:
    with open(file_path, 'rb') as f:
        offset = 0
        while offset < size:
            chunk = f.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            yield offset, chunk
            offset += len(chunk)
