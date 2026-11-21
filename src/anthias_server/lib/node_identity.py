"""The screen's long-lived, self-signed node certificate.

Every screen owns one ECDSA P-256 key and a self-signed certificate
for it, generated once and kept for ten years. It does two jobs:

* It is the default certificate of the web UI on ``:443`` until the
  operator uploads their own (see ``lib.web_certificate``).
* It is the screen's identity towards other Anthias nodes. A
  controller pins the SHA-256 fingerprint of this certificate, so it
  must never change behind the controller's back. Uploading a web
  certificate, enabling Let's Encrypt or reverting to the default
  never touches these files.

The files live under ``~/.anthias/ssl/node/``. That directory is not
part of a backup (``lib.backup_helper`` only ships a whitelist), so
restoring one screen's backup onto another cannot clone its identity.

This module is the only place that knows the paths; everything else
asks for them through :func:`ensure_node_identity`.
"""

import datetime
import fcntl
import hashlib
import logging
import os
import secrets
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)

VALIDITY_DAYS = 3650
_CERT_NAME = 'cert.pem'
_KEY_NAME = 'key.pem'


@dataclass(frozen=True)
class NodeIdentity:
    cert_path: Path
    key_path: Path
    fingerprint: str


def node_dir() -> Path:
    home = os.environ.get('HOME')
    if not home:
        raise OSError('HOME must be set to locate the node certificate.')
    return Path(home) / '.anthias' / 'ssl' / 'node'


def cert_path() -> Path:
    return node_dir() / _CERT_NAME


def key_path() -> Path:
    return node_dir() / _KEY_NAME


def cert_pem() -> bytes:
    return cert_path().read_bytes()


def certificate() -> x509.Certificate:
    return x509.load_pem_x509_certificate(cert_pem())


def fingerprint_of(certificate: x509.Certificate) -> str:
    der = certificate.public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(der).hexdigest()


def fingerprint() -> str:
    """Lowercase hex SHA-256 of the node certificate's DER, no colons."""
    return fingerprint_of(certificate())


def _common_name() -> str:
    # Not the hostname: inside the container that is the container ID,
    # which changes on every recreate and means nothing to anyone. The
    # name is cosmetic (peers pin the fingerprint), so a random suffix
    # fixed at generation time just tells two screens' certs apart.
    return f'Anthias node {secrets.token_hex(4)}'


def generate(now: datetime.datetime | None = None) -> tuple[bytes, bytes]:
    """Return a fresh (cert_pem, key_pem) pair."""
    now = now or datetime.datetime.now(datetime.UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    common_name = _common_name()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=VALIDITY_DAYS))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(common_name.lower().replace(' ', '-'))]
            ),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return certificate.public_bytes(serialization.Encoding.PEM), key_pem


def _write_private(target: Path, data: bytes, mode: int) -> None:
    # A unique temp name, so two writers never share (and truncate)
    # one temp file; the lock in ensure_node_identity() keeps them from
    # running at once anyway.
    fd, tmp = tempfile.mkstemp(prefix=f'.{target.name}.', dir=target.parent)
    try:
        os.fchmod(fd, mode)
        os.write(fd, data)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        os.unlink(tmp)
        raise
    os.close(fd)
    os.replace(tmp, target)


_MISSING = 'missing'
_INVALID = 'invalid'
_USABLE = 'usable'


def _state() -> str:
    """Whether the pair on disk is usable, missing, or damaged.

    Only a missing file or contents that do not parse count against
    the pair. Any other OSError (EIO on a failing SD card, EACCES)
    propagates: regenerating then would silently replace the identity
    every controller has pinned, over what may be a transient read
    error.
    """
    try:
        cert_bytes = cert_pem()
        key_bytes = key_path().read_bytes()
    except FileNotFoundError:
        return _MISSING
    try:
        certificate = x509.load_pem_x509_certificate(cert_bytes)
        key = serialization.load_pem_private_key(key_bytes, password=None)
    except (ValueError, TypeError):
        return _INVALID
    if certificate.public_key() != key.public_key():
        return _INVALID
    return _USABLE


_thread_lock = threading.Lock()


@contextmanager
def _generation_lock(directory: Path) -> Iterator[None]:
    """Serialise generation across threads and processes.

    The ASGI startup thread, a settings page and the CLI can all ask
    for the identity on first boot.
    """
    with _thread_lock:
        fd = os.open(directory / '.lock', os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def ensure_node_identity() -> NodeIdentity:
    """Create the node certificate if it is missing; return its paths.

    An existing, readable pair is never regenerated, even when it is
    close to expiry: replacing it would break every controller that
    pinned it. A pair whose contents cannot be parsed (a half-written
    file, a key that does not belong to the certificate) is replaced,
    since nothing can be pinned to a certificate the screen cannot
    serve. A pair that cannot be *read* raises instead.
    """
    directory = node_dir()
    if _state() != _USABLE:
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        with _generation_lock(directory):
            state = _state()
            if state != _USABLE:
                if state == _INVALID:
                    logger.warning(
                        'The node certificate under %s is damaged or does '
                        'not match its key; generating a new one.',
                        directory,
                    )
                new_cert, new_key = generate()
                _write_private(key_path(), new_key, 0o600)
                _write_private(cert_path(), new_cert, 0o644)
                logger.info('Generated the node certificate in %s', directory)
    return NodeIdentity(
        cert_path=cert_path(), key_path=key_path(), fingerprint=fingerprint()
    )
