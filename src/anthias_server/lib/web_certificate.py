"""The certificate the web UI serves on ``:443``.

Three modes, stored in ``~/.anthias/ssl/web/state.json``:

* ``default``: the screen's own node certificate
  (``lib.node_identity``). Browsers warn, because nobody vouches for
  it, but no setup is needed.
* ``custom``: a certificate + key the operator uploaded in Settings or
  installed with ``bin/enable_ssl.sh --cert/--key``. The full chain is
  stored leaf first in ``cert.pem``, next to ``key.pem``.
* ``acme``: Caddy obtains and renews a Let's Encrypt certificate for
  ``domain`` (``bin/enable_ssl.sh --domain``). Requests that reach the
  screen by another name or by IP get the node certificate.

None of these touch the node certificate. Machine-to-machine traffic
always uses that one, pinned by fingerprint, whatever the UI serves.
"""

import datetime
import ipaddress
import itertools
import json
import logging
import os
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import (
    dsa,
    ec,
    ed25519,
    rsa,
)

from anthias_server.lib import node_identity

logger = logging.getLogger(__name__)

MODE_DEFAULT = 'default'
MODE_CUSTOM = 'custom'
MODE_ACME = 'acme'
MODES = (MODE_DEFAULT, MODE_CUSTOM, MODE_ACME)

# Generous for a leaf plus a few intermediates, small enough that an
# upload cannot be used to fill the card.
MAX_PEM_BYTES = 64 * 1024

_HOSTNAME_RE = re.compile(
    r'^(?=.{1,253}$)'
    r'(?!-)[a-z0-9-]{1,63}(?<!-)'
    r'(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$'
)
_EMAIL_RE = re.compile(r'^[^@\s"\'{}]+@[^@\s"\'{}]+\.[^@\s"\'{}]+$')


class CertificateError(ValueError):
    """An uploaded certificate, key or chain was rejected."""


@dataclass(frozen=True)
class CertificateInfo:
    subject: str
    issuer: str
    sans: list[str]
    not_before: datetime.datetime
    not_after: datetime.datetime
    fingerprint: str
    self_signed: bool
    chain_length: int

    @property
    def days_left(self) -> int:
        now = datetime.datetime.now(datetime.UTC)
        return (self.not_after - now).days


@dataclass
class WebTlsState:
    mode: str = MODE_DEFAULT
    domain: str = ''
    email: str = ''
    staging: bool = False


@dataclass(frozen=True)
class ValidatedCertificate:
    fullchain_pem: bytes
    key_pem: bytes
    info: CertificateInfo


def web_dir() -> Path:
    return node_identity.node_dir().parent / 'web'


def cert_path() -> Path:
    return web_dir() / 'cert.pem'


def key_path() -> Path:
    return web_dir() / 'key.pem'


def state_path() -> Path:
    return web_dir() / 'state.json'


# --------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------


def _load_certs(data: bytes, what: str) -> list[x509.Certificate]:
    if len(data) > MAX_PEM_BYTES:
        raise CertificateError(f'The {what} file is too large.')
    if b'-----BEGIN' not in data:
        raise CertificateError(f'The {what} file is not in PEM format.')
    try:
        return x509.load_pem_x509_certificates(data)
    except ValueError as exc:
        raise CertificateError(
            f'The {what} file does not contain a readable PEM certificate.'
        ) from exc


def _load_key(
    data: bytes,
) -> (
    rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey | ed25519.Ed25519PrivateKey
):
    if len(data) > MAX_PEM_BYTES:
        raise CertificateError('The private key file is too large.')
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except TypeError as exc:
        raise CertificateError(
            'The private key is protected by a passphrase. Upload an '
            'unencrypted key.'
        ) from exc
    except ValueError as exc:
        raise CertificateError(
            'The private key file does not contain a readable PEM key.'
        ) from exc
    if isinstance(key, dsa.DSAPrivateKey) or not isinstance(
        key,
        rsa.RSAPrivateKey
        | ec.EllipticCurvePrivateKey
        | ed25519.Ed25519PrivateKey,
    ):
        raise CertificateError(
            'Unsupported key type. Use an RSA, ECDSA or Ed25519 key.'
        )
    return key


def _sans(certificate: x509.Certificate) -> list[str]:
    try:
        ext = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        )
    except x509.ExtensionNotFound:
        return []
    names: list[str] = list(ext.value.get_values_for_type(x509.DNSName))
    names += [str(ip) for ip in ext.value.get_values_for_type(x509.IPAddress)]
    return names


def describe(
    certificate: x509.Certificate, chain_length: int = 1
) -> CertificateInfo:
    return CertificateInfo(
        subject=certificate.subject.rfc4514_string(),
        issuer=certificate.issuer.rfc4514_string(),
        sans=_sans(certificate),
        not_before=certificate.not_valid_before_utc,
        not_after=certificate.not_valid_after_utc,
        fingerprint=node_identity.fingerprint_of(certificate),
        self_signed=certificate.issuer == certificate.subject,
        chain_length=chain_length,
    )


def validate(
    cert_pem: bytes,
    key_pem: bytes,
    chain_pem: bytes | None = None,
    now: datetime.datetime | None = None,
) -> ValidatedCertificate:
    """Check an upload before it goes anywhere near Caddy.

    ``cert_pem`` may already carry the chain after the leaf (a
    ``fullchain.pem``); ``chain_pem`` is appended after it. Raises
    :class:`CertificateError` with an operator-facing message.
    """
    now = now or datetime.datetime.now(datetime.UTC)
    certs = _load_certs(cert_pem, 'certificate')
    if chain_pem and chain_pem.strip():
        certs += _load_certs(chain_pem, 'chain')
    if len(certs) > 10:
        raise CertificateError('The chain has too many certificates.')
    leaf = certs[0]
    key = _load_key(key_pem)

    if leaf.public_key() != key.public_key():
        raise CertificateError(
            'The private key does not belong to the certificate.'
        )

    if leaf.not_valid_after_utc <= now:
        raise CertificateError(
            f'The certificate expired on {leaf.not_valid_after_utc:%Y-%m-%d}.'
        )
    if leaf.not_valid_before_utc > now:
        raise CertificateError(
            'The certificate is not valid until '
            f'{leaf.not_valid_before_utc:%Y-%m-%d}.'
        )

    for position, (child, parent) in enumerate(
        itertools.pairwise(certs), start=1
    ):
        if child.issuer != parent.subject:
            raise CertificateError(
                f'The chain is out of order: certificate {position + 1} '
                f'did not issue certificate {position}. Put the leaf '
                'first, then each issuer in turn.'
            )
        try:
            child.verify_directly_issued_by(parent)
        except (InvalidSignature, ValueError, TypeError) as exc:
            raise CertificateError(
                f'Certificate {position} is not signed by certificate '
                f'{position + 1} in the chain.'
            ) from exc
        if parent.not_valid_after_utc <= now:
            raise CertificateError(
                f'Chain certificate {position + 1} expired on '
                f'{parent.not_valid_after_utc:%Y-%m-%d}.'
            )

    fullchain = b''.join(
        c.public_bytes(serialization.Encoding.PEM) for c in certs
    )
    normalized_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return ValidatedCertificate(
        fullchain_pem=fullchain,
        key_pem=normalized_key,
        info=describe(leaf, chain_length=len(certs)),
    )


def validate_domain(domain: str) -> str:
    domain = domain.strip().lower().rstrip('.')
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        raise CertificateError(
            "Let's Encrypt needs a domain name, not an IP address."
        )
    if not _HOSTNAME_RE.match(domain):
        raise CertificateError(f'Not a valid domain name: {domain}')
    return domain


def validate_email(email: str) -> str:
    email = email.strip()
    if email and not _EMAIL_RE.match(email):
        raise CertificateError(f'Not a valid email address: {email}')
    return email


# --------------------------------------------------------------------
# State
# --------------------------------------------------------------------


def _write(target: Path, data: bytes, mode: int) -> None:
    tmp = target.with_name(f'.{target.name}.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, target)


def _ensure_dir() -> None:
    web_dir().mkdir(parents=True, exist_ok=True)
    os.chmod(web_dir(), 0o700)


def load_state() -> WebTlsState:
    try:
        raw = json.loads(state_path().read_text())
    except FileNotFoundError:
        return WebTlsState()
    except (OSError, ValueError):
        logger.warning('Unreadable %s; using the default cert.', state_path())
        return WebTlsState()
    mode = raw.get('mode', MODE_DEFAULT)
    if mode not in MODES:
        mode = MODE_DEFAULT
    state = WebTlsState(
        mode=mode,
        domain=str(raw.get('domain') or ''),
        email=str(raw.get('email') or ''),
        staging=bool(raw.get('staging', False)),
    )
    if state.mode == MODE_CUSTOM and not (
        cert_path().is_file() and key_path().is_file()
    ):
        logger.warning(
            'The uploaded web certificate is missing; serving the node '
            'certificate instead.'
        )
        return WebTlsState()
    if state.mode == MODE_ACME and not state.domain:
        return WebTlsState()
    return state


def save_state(state: WebTlsState) -> None:
    _ensure_dir()
    payload = asdict(state)
    _write(
        state_path(),
        (json.dumps(payload, indent=2) + '\n').encode(),
        0o600,
    )


def install_custom(validated: ValidatedCertificate) -> None:
    _ensure_dir()
    _write(key_path(), validated.key_pem, 0o600)
    _write(cert_path(), validated.fullchain_pem, 0o644)
    save_state(WebTlsState(mode=MODE_CUSTOM))


def set_acme(domain: str, email: str = '', staging: bool = False) -> None:
    save_state(
        WebTlsState(
            mode=MODE_ACME,
            domain=validate_domain(domain),
            email=validate_email(email),
            staging=staging,
        )
    )


def revert_to_default() -> None:
    save_state(WebTlsState())
    for path in (cert_path(), key_path()):
        path.unlink(missing_ok=True)


def current_custom_info() -> CertificateInfo | None:
    try:
        certs = x509.load_pem_x509_certificates(cert_path().read_bytes())
    except (OSError, ValueError):
        return None
    return describe(certs[0], chain_length=len(certs))


@dataclass
class Snapshot:
    """What is on disk now, so a rejected apply can be rolled back."""

    state: bytes | None
    cert: bytes | None
    key: bytes | None


def snapshot() -> Snapshot:
    def read(path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    return Snapshot(
        state=read(state_path()),
        cert=read(cert_path()),
        key=read(key_path()),
    )


def restore(saved: Snapshot) -> None:
    _ensure_dir()
    for path, data, mode in (
        (state_path(), saved.state, 0o600),
        (cert_path(), saved.cert, 0o644),
        (key_path(), saved.key, 0o600),
    ):
        if data is None:
            path.unlink(missing_ok=True)
        else:
            _write(path, data, mode)


# --------------------------------------------------------------------
# Migration from bin/enable_ssl.sh's old layout
# --------------------------------------------------------------------

_LEGACY_SITE_RE = re.compile(r'^([A-Za-z0-9.-]+)\s*\{\s*$', re.MULTILINE)
_LEGACY_EMAIL_RE = re.compile(r'^\s*email\s+(\S+)\s*$', re.MULTILINE)


def migrate_legacy_layout() -> str | None:
    """Carry an old ``enable_ssl.sh`` setup over to the new layout.

    Before Caddy was always on, ``enable_ssl.sh`` wrote a Caddyfile to
    ``~/.anthias/Caddyfile`` and, in bring-your-own mode, copied the
    cert and key to ``~/.anthias/ssl/{cert,key}.pem``. Those installs
    keep the certificate they chose:

    * BYO cert: installed as the ``custom`` web cert if it still
      validates.
    * ``--domain``: becomes ``acme`` mode for the same domain/email.
    * Local-CA mode: dropped. The node certificate replaces it.

    Runs once: the Caddyfile is renamed to ``Caddyfile.migrated`` and
    the old cert files are moved out of the way. Returns a short
    description of what was done, or None.
    """
    anthias_dir = node_identity.node_dir().parent.parent
    caddyfile = anthias_dir / 'Caddyfile'
    legacy_cert = node_identity.node_dir().parent / 'cert.pem'
    legacy_key = node_identity.node_dir().parent / 'key.pem'
    if state_path().exists():
        return None

    outcome: str | None = None
    if legacy_cert.is_file() and legacy_key.is_file():
        try:
            validated = validate(
                legacy_cert.read_bytes(), legacy_key.read_bytes()
            )
        except CertificateError as exc:
            logger.warning(
                'Not migrating the old enable_ssl.sh certificate: %s', exc
            )
            outcome = 'legacy certificate rejected; using node certificate'
        else:
            install_custom(validated)
            outcome = 'migrated enable_ssl.sh certificate'
        for path in (legacy_cert, legacy_key):
            shutil.move(path, path.with_name(f'{path.name}.migrated'))
    elif caddyfile.is_file():
        text = caddyfile.read_text(errors='replace')
        domains = [
            m
            for m in _LEGACY_SITE_RE.findall(text)
            if not m.startswith(':') and '.' in m
        ]
        if domains:
            email_match = _LEGACY_EMAIL_RE.search(text)
            try:
                set_acme(
                    domains[0],
                    email_match.group(1) if email_match else '',
                    staging='acme-staging' in text,
                )
                outcome = f"migrated Let's Encrypt domain {domains[0]}"
            except CertificateError as exc:
                logger.warning('Not migrating the old domain: %s', exc)

    if caddyfile.is_file():
        caddyfile.rename(caddyfile.with_name('Caddyfile.migrated'))
        if outcome is None:
            outcome = 'dropped local-CA mode; using node certificate'
    if outcome:
        logger.info('HTTPS migration: %s', outcome)
    return outcome
