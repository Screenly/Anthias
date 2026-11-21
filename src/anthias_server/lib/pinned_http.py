"""HTTPS to another Anthias node, pinned to its certificate fingerprint.

Nodes present long-lived self-signed certificates
(:mod:`anthias_server.lib.node_identity`), so there is no CA chain to
check. Instead the peer's leaf certificate must hash (SHA-256 over the
DER) to the fingerprint the caller already trusts. urllib3 checks
``assert_fingerprint`` during the TLS handshake, before any request
bytes are sent, so a credential in the request never reaches a peer
with a different certificate.

Two details that are easy to get wrong:

* ``requests.HTTPAdapter.cert_verify()`` puts ``cert_reqs`` back to
  ``CERT_REQUIRED`` whenever ``verify=True``, which then fails on the
  self-signed chain before the fingerprint is ever compared. The
  adapter overrides it so chain verification stays off and the
  fingerprint is the only check.
* ``trust_env`` is off, so an ``HTTPS_PROXY`` in the environment can't
  carry the connection (and the pin) somewhere else.
"""

from __future__ import annotations

import contextlib
import contextvars
import re
import socket
import threading
from collections.abc import Iterator
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool

_FINGERPRINT = re.compile(r'^[0-9a-f]{64}$')


class PinnedFingerprintError(ValueError):
    pass


def normalize_fingerprint(value: str) -> str:
    """Lowercase hex without separators; raises if it isn't SHA-256."""
    cleaned = (value or '').strip().lower().replace(':', '')
    if not _FINGERPRINT.match(cleaned):
        raise PinnedFingerprintError(
            'A certificate fingerprint is 64 hex characters (SHA-256).'
        )
    return cleaned


class RequestDeadlineError(requests.exceptions.Timeout):
    """The entire request exhausted its wall-clock budget."""


class _RequestDeadline:
    def __init__(self, seconds: float) -> None:
        self.expired = False
        self._lock = threading.Lock()
        self._sockets: list[socket.socket] = []
        self.timer = threading.Timer(seconds, self._expire)

    def watch(self, sock: socket.socket | None) -> None:
        if sock is None:
            return
        with self._lock:
            if self.expired:
                raise RequestDeadlineError(
                    'The screen took too long to answer.'
                )
            if sock not in self._sockets:
                self._sockets.append(sock)

    def _expire(self) -> None:
        with self._lock:
            self.expired = True
            for sock in self._sockets:
                # close() alone does not interrupt a makefile() read.
                # shutdown wakes header/body reads even when the peer
                # keeps sending inside the inactivity timeout.
                with contextlib.suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)


_deadline: contextvars.ContextVar[_RequestDeadline | None] = (
    contextvars.ContextVar('pinned_http_deadline', default=None)
)


@contextlib.contextmanager
def request_deadline(seconds: float) -> Iterator[None]:
    """Interrupt pinned HTTP I/O after a total wall-clock budget.

    Join the timer before returning to the caller, so it cannot fire
    during the next request on this session. No reader is abandoned
    in a worker thread.
    """
    guard = _RequestDeadline(seconds)
    token = _deadline.set(guard)
    guard.timer.start()
    try:
        yield
    finally:
        guard.timer.cancel()
        guard.timer.join()
        _deadline.reset(token)
        if guard.expired:
            raise RequestDeadlineError('The screen took too long to answer.')


class _DeadlineHTTPSConnection(HTTPSConnection):
    def _watch_socket(self) -> None:
        guard = _deadline.get()
        if guard is not None:
            guard.watch(self.sock)

    def connect(self) -> None:
        super().connect()
        self._watch_socket()

    def request(self, *args: Any, **kwargs: Any) -> None:
        # Reused connections already have a socket; new ones register
        # theirs in connect(), before sending HTTP bytes.
        self._watch_socket()
        super().request(*args, **kwargs)


class _DeadlineHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _DeadlineHTTPSConnection


class FingerprintAdapter(HTTPAdapter):
    def __init__(self, fingerprint: str, **kwargs: Any) -> None:
        self.fingerprint = normalize_fingerprint(fingerprint)
        super().__init__(**kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs['assert_fingerprint'] = self.fingerprint
        kwargs['cert_reqs'] = 'CERT_NONE'
        kwargs['assert_hostname'] = False
        super().init_poolmanager(*args, **kwargs)
        # Copy rather than mutate urllib3's process-wide registry.
        self.poolmanager.pool_classes_by_scheme = {
            **self.poolmanager.pool_classes_by_scheme,
            'https': _DeadlineHTTPSConnectionPool,
        }

    def cert_verify(self, conn: Any, url: str, verify: Any, cert: Any) -> None:
        super().cert_verify(conn, url, False, cert)
        conn.cert_reqs = 'CERT_NONE'
        conn.assert_fingerprint = self.fingerprint
        conn.assert_hostname = False


def pinned_session(
    fingerprint: str, client_cert: tuple[str, str] | None = None
) -> requests.Session:
    """A session whose https:// requests only reach ``fingerprint``.

    Plain http:// is refused outright: a pin means nothing without TLS.
    ``client_cert`` is a (certificate, key) file pair to present as the
    TLS client certificate (mutual TLS; see ``lib.pki``).
    """
    session = requests.Session()
    if client_cert is not None:
        session.cert = client_cert
    session.trust_env = False
    session.mount('https://', FingerprintAdapter(fingerprint))
    session.mount('http://', _RefuseAdapter())
    return session


class _RefuseAdapter(HTTPAdapter):
    def send(self, *_args: Any, **_kwargs: Any) -> requests.Response:
        raise requests.exceptions.InvalidSchema(
            'Pinned sessions only speak https.'
        )
