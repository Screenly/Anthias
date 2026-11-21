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

import re
from typing import Any

import requests
from requests.adapters import HTTPAdapter

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


class FingerprintAdapter(HTTPAdapter):
    def __init__(self, fingerprint: str, **kwargs: Any) -> None:
        self.fingerprint = normalize_fingerprint(fingerprint)
        super().__init__(**kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs['assert_fingerprint'] = self.fingerprint
        kwargs['cert_reqs'] = 'CERT_NONE'
        kwargs['assert_hostname'] = False
        super().init_poolmanager(*args, **kwargs)

    def cert_verify(self, conn: Any, url: str, verify: Any, cert: Any) -> None:
        super().cert_verify(conn, url, False, cert)
        conn.cert_reqs = 'CERT_NONE'
        conn.assert_fingerprint = self.fingerprint
        conn.assert_hostname = False


def pinned_session(fingerprint: str) -> requests.Session:
    """A session whose https:// requests only reach ``fingerprint``.

    Plain http:// is refused outright: a pin means nothing without TLS.
    """
    session = requests.Session()
    session.trust_env = False
    session.mount('https://', FingerprintAdapter(fingerprint))
    session.mount('http://', _RefuseAdapter())
    return session


class _RefuseAdapter(HTTPAdapter):
    def send(self, *_args: Any, **_kwargs: Any) -> requests.Response:
        raise requests.exceptions.InvalidSchema(
            'Pinned sessions only speak https.'
        )
