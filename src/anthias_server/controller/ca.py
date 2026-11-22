"""This controller's certificate authority, and its revocation list.

Created the first time it is needed in controller mode, under
``~/.anthias/ssl/controller/`` next to the node certificate. Like the
node certificate and the vault key it is **not** part of backups: a
restored backup must not clone the authority that vouches for this
controller's screens onto another device. Replacing a controller
therefore means its screens join again.

What it signs (see :mod:`anthias_server.lib.pki`):

* a screen's client certificate, when the operator approves its join
  request and again when the screen renews it;
* this controller's own client certificate, presented on every call to
  a screen, renewed here automatically.

Every certificate it replaces or takes back is revoked: listed in
:class:`~.models.RevokedCertificate` and in the signed CRL
(:func:`crl_pem`) that the controller hands to its screens. The CRL is
re-signed well before it expires and whenever the list changes, and
connected screens get each new one at once over their push connection.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from django.utils import timezone

from anthias_server.lib import pki

logger = logging.getLogger(__name__)

_lock = threading.RLock()


class CaError(Exception):
    pass


def ca_dir() -> Path:
    home = os.environ.get('HOME')
    if not home:
        raise CaError('HOME must be set to locate the controller CA.')
    return Path(home) / '.anthias' / 'ssl' / 'controller'


def ca_key_path() -> Path:
    return ca_dir() / 'ca.key'


def ca_cert_path() -> Path:
    return ca_dir() / 'ca.crt'


def client_cert_path() -> Path:
    """This controller's own client certificate (key: the node key)."""
    return ca_dir() / 'client.crt'


def crl_path() -> Path:
    return ca_dir() / 'crl.pem'


def _write(target: Path, data: bytes, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    tmp = target.with_name(f'.{target.name}.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, target)


def _load() -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey] | None:
    try:
        cert = pki.load_cert(ca_cert_path().read_bytes())
        key = pki.load_key(ca_key_path().read_bytes())
    except (OSError, ValueError):
        return None
    if not pki.same_public_key(cert.public_key(), key):
        return None
    return cert, key


def ensure_ca() -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    """The CA certificate and key, created on first use."""
    from anthias_server.lib import management

    with _lock:
        loaded = _load()
        if loaded is not None:
            return loaded
        if ca_cert_path().exists() or ca_key_path().exists():
            logger.warning('Replacing an unusable controller CA')
        key = pki.new_key()
        fingerprint = management.node_fingerprint()
        cert = pki.make_ca(key, f'Anthias controller CA {fingerprint[:12]}')
        _write(ca_key_path(), pki.key_pem(key), 0o600)
        _write(ca_cert_path(), pki.cert_pem(cert).encode(), 0o644)
        # A new authority: whatever the old one issued is meaningless.
        for path in (client_cert_path(), crl_path()):
            path.unlink(missing_ok=True)
        logger.info('Created the controller CA %s', pki.fingerprint(cert))
        return cert, key


def ca_cert() -> x509.Certificate:
    return ensure_ca()[0]


def ca_pem() -> str:
    return pki.cert_pem(ca_cert())


def ca_fingerprint() -> str:
    return pki.fingerprint(ca_cert())


# --- issuing ----------------------------------------------------------------


def sign_screen_csr(
    csr_pem: str, screen_public_key: object, screen_fingerprint: str
) -> x509.Certificate:
    """A screen client certificate for an approved request or renewal.

    The CSR must be signed by, and carry, the screen's own node key
    (``screen_public_key``, from its pinned certificate), so a
    certificate can only ever be issued for the key that screen
    proved it holds.
    """
    csr = pki.load_csr(csr_pem)
    if not pki.same_public_key(csr.public_key(), screen_public_key):
        raise pki.PkiError("The signing request is not for this screen's key.")
    cert, key = ensure_ca()
    return pki.issue_client_cert(
        cert, key, csr.public_key(), screen_fingerprint, pki.ROLE_SCREEN
    )


def ensure_client_cert() -> tuple[str, str]:
    """(cert, key) paths of this controller's client certificate.

    Issued for the node key, and reissued when missing, from another
    CA, or close to expiry; the replaced one is revoked.
    """
    from anthias_server.lib import node_identity

    identity = node_identity.ensure_node_identity()
    with _lock:
        cert, key = ensure_ca()
        node_key = pki.load_key(identity.key_path.read_bytes())
        try:
            current: x509.Certificate | None = pki.load_cert(
                client_cert_path().read_bytes()
            )
        except (OSError, pki.PkiError):
            current = None
        if current is not None:
            try:
                pki.check_client_cert(
                    current, cert, pki.ROLE_CONTROLLER, public_key=node_key
                )
                usable = not pki.needs_renewal(current) and not is_revoked(
                    pki.serial_hex(current.serial_number)
                )
            except pki.PkiError:
                usable = False
            if usable:
                return str(client_cert_path()), str(identity.key_path)
        issued = pki.issue_client_cert(
            cert,
            key,
            node_key.public_key(),
            identity.fingerprint,
            pki.ROLE_CONTROLLER,
        )
        _write(client_cert_path(), pki.cert_pem(issued).encode(), 0o644)
        if current is not None and pki.signed_by(current, cert):
            revoke(
                pki.serial_hex(current.serial_number),
                'controller client certificate',
                'superseded',
                current.not_valid_after_utc,
            )
    logger.info('Issued the controller client certificate')
    return str(client_cert_path()), str(identity.key_path)


# --- revocation -------------------------------------------------------------


def is_revoked(serial: str) -> bool:
    from .models import RevokedCertificate

    return RevokedCertificate.objects.filter(serial=serial.lower()).exists()


def revoke(
    serial: str,
    label: str,
    reason: str,
    not_after: datetime | None = None,
) -> bool:
    """Revoke a certificate this CA issued. True if newly revoked.

    The new CRL goes out to every connected screen at once.
    """
    from .models import RevokedCertificate

    if not serial:
        return False
    _, created = RevokedCertificate.objects.get_or_create(
        serial=serial.lower(),
        defaults={
            'label': label[:200],
            'reason': reason[:200],
            'not_after': not_after,
        },
    )
    if created:
        logger.info('Revoked certificate %s (%s): %s', serial, label, reason)
        crl_path().unlink(missing_ok=True)
        _broadcast_crl()
    return created


def _revoked_rows() -> list[tuple[int, datetime]]:
    from .models import RevokedCertificate

    now = timezone.now()
    rows = []
    for row in RevokedCertificate.objects.all():
        # Once it would have expired anyway it no longer needs listing.
        if row.not_after is not None and row.not_after < now:
            continue
        rows.append((int(row.serial, 16), row.revoked_at))
    return rows


def _revocation_digest(serials: Iterable[int]) -> str:
    text = ','.join(sorted(pki.serial_hex(s) for s in serials))
    return hashlib.sha256(text.encode()).hexdigest()


def crl_pem() -> str:
    """The current signed CRL, re-signed when stale or changed."""
    with _lock:
        cert, key = ensure_ca()
        rows = _revoked_rows()
        digest = _revocation_digest(serial for serial, _ in rows)
        try:
            text = crl_path().read_text()
            crl = pki.load_crl(text)
            pki.check_crl(crl, cert)
            fresh = (
                crl.next_update_utc is not None
                and crl.next_update_utc - pki.now() > pki.CRL_REFRESH_BEFORE
            )
            same = _revocation_digest(pki.revoked_serials(crl)) == digest
            if fresh and same:
                return text
        except (OSError, pki.PkiError):
            pass
        # Seconds since the epoch: strictly increasing between re-signs,
        # which is all CRLNumber has to be.
        crl = pki.make_crl(cert, key, rows, number=int(time.time()))
        text = pki.crl_pem(crl)
        _write(crl_path(), text.encode(), 0o644)
        return text


def refresh_crl() -> bool:
    """Re-sign the CRL if due. True (and broadcast) if it changed."""
    try:
        before = crl_path().read_text()
    except OSError:
        before = ''
    after = crl_pem()
    if after != before:
        _broadcast_crl()
        return True
    return False


def _broadcast_crl() -> None:
    from .push import broadcast_crl

    try:
        broadcast_crl()
    except Exception:
        logger.exception('Could not send the new CRL to connected screens')
