"""This controller's certificate authority, and its revocation list.

Created the first time it is needed in controller mode, under
``~/.anthias/ssl/controller/`` next to the node certificate. Like the
node certificate and the vault key it is not part of a normal backup:
a copied backup must not clone the authority that vouches for this
controller's screens. The operator can add all three, encrypted with
a password, to move a controller to new hardware without its screens
joining again (``lib.identity_backup``).

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
Its number comes from a counter in the database
(:class:`~.models.CrlCounter`), never the clock. Revocations stop being
listed, and their rows are pruned, once the certificate has expired.

**The CA fails closed.** It is created only when neither ``ca.key``
nor ``ca.crt`` exists, under a file lock shared by the web server and
Celery. If either file exists but cannot be read or parsed (an SD-card
read error, a half-restored backup), :class:`CaUnavailableError` is
raised and nothing is replaced: a new CA would silently cut off every
managed screen, which then has to join again by hand. The Screens page
shows the error (:func:`problem`) until an operator fixes the files.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import logging
import os
import threading
from collections.abc import Iterable, Iterator
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


class CaUnavailableError(CaError):
    """The CA files exist but cannot be used. Never auto-replaced."""


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


def lock_path() -> Path:
    return ca_dir() / '.ca.lock'


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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
    _fsync_dir(target.parent)


def _create(target: Path, data: bytes, mode: int) -> None:
    """Write a file that must not exist yet: fsync, then link it in.

    ``os.link`` fails if the target appeared meanwhile, so a CA file
    is never overwritten, even by a process that ignored the lock.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    tmp = target.with_name(f'.{target.name}.{os.getpid()}.new')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    _fsync_dir(target.parent)


@contextlib.contextmanager
def _file_lock() -> Iterator[None]:
    """Exclusive across processes (web server, Celery) and threads."""
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # releases the lock


_UNREADABLE = (
    'The controller CA in {dir} exists but could not be used ({why}). '
    'It was left untouched: replacing it would cut off every managed '
    'screen. Restore ca.key and ca.crt from a backup with device keys, '
    'or remove both files to start a new CA (every screen then has to '
    'join again).'
)


def _unavailable(why: str) -> CaUnavailableError:
    message = _UNREADABLE.format(dir=ca_dir(), why=why)
    logger.error('%s', message)
    return CaUnavailableError(message)


def _read(path: Path) -> bytes | None:
    """The file's bytes; None only if it does not exist."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _unavailable(f'{path.name}: {exc.strerror or exc}') from exc


def _load() -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey] | None:
    """The CA, or None when neither file exists.

    Raises CaUnavailableError when there is something on disk that is
    not a usable CA. The one repair made here: a readable key without
    its certificate (a crash between writing the two at creation, when
    the certificate had not been handed to any screen yet) gets its
    certificate made again from the same key.
    """
    key_bytes = _read(ca_key_path())
    cert_bytes = _read(ca_cert_path())
    if key_bytes is None and cert_bytes is None:
        return None
    if key_bytes is None:
        raise _unavailable('ca.key is missing')
    try:
        key = pki.load_key(key_bytes)
    except (ValueError, TypeError) as exc:
        raise _unavailable('ca.key is not a usable key') from exc
    if cert_bytes is None:
        return None
    try:
        cert = pki.load_cert(cert_bytes)
    except ValueError as exc:
        raise _unavailable('ca.crt is not a certificate') from exc
    if not pki.same_public_key(cert.public_key(), key):
        raise _unavailable('ca.crt does not belong to ca.key')
    return cert, key


def _make(
    key: ec.EllipticCurvePrivateKey | None,
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    from anthias_server.lib import management

    fingerprint = management.node_fingerprint()
    if key is None:
        key = pki.new_key()
        _create(ca_key_path(), pki.key_pem(key), 0o600)
        # A new authority: whatever an older one issued is meaningless.
        for path in (client_cert_path(), crl_path()):
            path.unlink(missing_ok=True)
    cert = pki.make_ca(key, f'Anthias controller CA {fingerprint[:12]}')
    _create(ca_cert_path(), pki.cert_pem(cert).encode(), 0o644)
    logger.info('Created the controller CA %s', pki.fingerprint(cert))
    return cert, key


def ensure_ca() -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    """The CA certificate and key, created on first use.

    Raises CaUnavailableError (see the module docstring) rather than
    ever replacing CA files that exist.
    """
    with _lock:
        loaded = _load()
        if loaded is not None:
            return loaded
        with _file_lock():
            # Another process may have created it while we waited.
            loaded = _load()
            if loaded is not None:
                return loaded
            key_bytes = _read(ca_key_path())
            key = pki.load_key(key_bytes) if key_bytes is not None else None
            if key is not None:
                logger.warning(
                    'The controller CA key has no certificate; making it'
                )
            return _make(key)


def problem() -> str:
    """Why the CA is unusable, for the Screens page; '' when it is fine.

    Only reads: never creates the CA.
    """
    try:
        _load()
    except CaError as exc:
        return str(exc)
    return ''


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


def issue_for_screen(
    node_public_key_pem: str, screen_fingerprint: str
) -> x509.Certificate:
    """A renewed screen certificate for the key bound at join.

    For the controller-driven renewal, where there is no CSR: the key
    is the one the screen proved it holds when it joined
    (``Node.public_key_pem``), so the certificate is useless to anyone
    else.
    """
    public_key = pki.load_public_key(node_public_key_pem)
    cert, key = ensure_ca()
    return pki.issue_client_cert(
        cert, key, public_key, screen_fingerprint, pki.ROLE_SCREEN
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
        # The CRL on disk is kept: crl_pem() sees the list changed and
        # re-signs it, continuing from its number.
        _broadcast_crl()
    return created


def prune_revocations() -> int:
    """Delete revocation rows nothing needs any more. Returns how many.

    A certificate past its expiry is refused anyway, so its row no
    longer goes into the CRL. Rows without an expiry are kept for one
    certificate lifetime after the revocation, which covers anything
    the CA can have issued before it.
    """
    from django.db.models import Q

    from .models import RevokedCertificate

    now = timezone.now()
    stale = Q(not_after__lt=now) | Q(
        not_after__isnull=True,
        revoked_at__lt=now - pki.CLIENT_VALIDITY - pki.CLOCK_SKEW,
    )
    deleted, _ = RevokedCertificate.objects.filter(stale).delete()
    if deleted:
        logger.info('Pruned %s expired revocation(s)', deleted)
    return deleted


def _revoked_rows() -> list[tuple[int, datetime]]:
    from .models import RevokedCertificate

    prune_revocations()
    return [
        (int(row.serial, 16), row.revoked_at)
        for row in RevokedCertificate.objects.all()
    ]


def _next_crl_number(current: int) -> int:
    """The next CRL number: greater than any used before, from the DB.

    ``current`` is the number of the CRL on disk, if any, so even a
    counter row lost with a database restore keeps the sequence going.
    """
    from django.db import transaction

    from .models import CrlCounter

    with transaction.atomic():
        row, _ = CrlCounter.objects.select_for_update().get_or_create(pk=1)
        row.number = max(row.number, current) + 1
        row.save(update_fields=['number'])
    return row.number


def _revocation_digest(serials: Iterable[int]) -> str:
    text = ','.join(sorted(pki.serial_hex(s) for s in serials))
    return hashlib.sha256(text.encode()).hexdigest()


def crl_pem() -> str:
    """The current signed CRL, re-signed when stale or changed."""
    with _lock:
        cert, key = ensure_ca()
        rows = _revoked_rows()
        digest = _revocation_digest(serial for serial, _ in rows)
        current = 0
        try:
            text = crl_path().read_text()
            crl = pki.load_crl(text)
            pki.check_crl(crl, cert)
            current = pki.crl_number(crl)
            fresh = (
                crl.next_update_utc is not None
                and crl.next_update_utc - pki.now() > pki.CRL_REFRESH_BEFORE
            )
            same = _revocation_digest(pki.revoked_serials(crl)) == digest
            if fresh and same:
                return text
        except (OSError, pki.PkiError):
            pass
        crl = pki.make_crl(cert, key, rows, _next_crl_number(current))
        text = pki.crl_pem(crl)
        if len(text) > pki.MAX_CRL_BYTES * 3 // 4:
            logger.warning(
                'The revocation list is %s bytes, close to the %s-byte '
                'limit screens accept (%s entries)',
                len(text),
                pki.MAX_CRL_BYTES,
                len(rows),
            )
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
