"""The small private PKI behind multi-screen management.

A controller is a certificate authority for the screens it manages
(``anthias_server.controller.ca`` owns its key). It signs:

* each screen's **client certificate**, from a CSR the screen builds
  with its own node key when it asks to join, once the operator
  approves the request;
* its own **controller client certificate**, which it presents when it
  calls a screen.

Both are client-authentication certificates only (no CA, no server
use) and carry their role in the subject's organizational unit, so a
screen can tell the controller's certificate from another screen's
even though both chain to the same CA. Revoked certificates are listed
in a signed X.509 CRL that the controller hands to every screen.

Server identity is unchanged: every node still serves its self-signed
node certificate (``lib.node_identity``), and peers still pin its
fingerprint. The CA adds client authentication in both directions.

Everything here is pure ``cryptography`` code without I/O, so both
sides share it and it is easy to test.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from collections.abc import Iterable

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ORG = 'Anthias'
ROLE_CONTROLLER = 'anthias-controller'
ROLE_SCREEN = 'anthias-screen'
ROLES = frozenset({ROLE_CONTROLLER, ROLE_SCREEN})

CA_VALIDITY = dt.timedelta(days=3650)
CLIENT_VALIDITY = dt.timedelta(days=365)
# Renew a client certificate once less than this much validity is left.
RENEW_BEFORE = dt.timedelta(days=30)
# A CRL is valid for a week and re-signed well before that.
CRL_VALIDITY = dt.timedelta(days=7)
CRL_REFRESH_BEFORE = dt.timedelta(days=2)
# Issued certificates may start a little in the past so a peer whose
# clock is slightly behind still accepts them.
CLOCK_SKEW = dt.timedelta(minutes=5)
MAX_PEM_BYTES = 16 * 1024
_EC_ONLY = 'Only EC keys are used here.'


class PkiError(ValueError):
    """A certificate, key, CSR or CRL is unusable for its purpose."""


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# --- encoding ---------------------------------------------------------------


def fingerprint(cert: x509.Certificate) -> str:
    """Lowercase hex SHA-256 of the DER, like node fingerprints."""
    return hashlib.sha256(
        cert.public_bytes(serialization.Encoding.DER)
    ).hexdigest()


def serial_hex(serial: int) -> str:
    return format(serial, 'x')


def cert_pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode('ascii')


def key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _pem_bytes(pem: str | bytes, what: str) -> bytes:
    data = pem.encode('ascii', 'ignore') if isinstance(pem, str) else pem
    if not data or len(data) > MAX_PEM_BYTES:
        raise PkiError(f'The {what} is missing or too large.')
    return data


def load_cert(pem: str | bytes) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(_pem_bytes(pem, 'certificate'))
    except ValueError as exc:
        raise PkiError('That is not a PEM certificate.') from exc


def load_csr(pem: str | bytes) -> x509.CertificateSigningRequest:
    try:
        csr = x509.load_pem_x509_csr(_pem_bytes(pem, 'signing request'))
    except ValueError as exc:
        raise PkiError(
            'That is not a PEM certificate signing request.'
        ) from exc
    if not csr.is_signature_valid:
        raise PkiError('The signing request is not signed by its own key.')
    return csr


def load_crl(pem: str | bytes) -> x509.CertificateRevocationList:
    try:
        return x509.load_pem_x509_crl(_pem_bytes(pem, 'revocation list'))
    except ValueError as exc:
        raise PkiError('That is not a PEM revocation list.') from exc


def load_key(pem: bytes) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise PkiError(_EC_ONLY)
    return key


def public_key_bytes(key: object) -> bytes:
    """SubjectPublicKeyInfo DER of a public key, or of a private key's."""
    if isinstance(key, ec.EllipticCurvePrivateKey):
        key = key.public_key()
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise PkiError(_EC_ONLY)
    return key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def public_key_pem(key: object) -> str:
    """PEM SubjectPublicKeyInfo, to store a screen's node public key."""
    der = public_key_bytes(key)
    public = serialization.load_der_public_key(der)
    return public.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode('ascii')


def load_public_key(pem: str) -> object:
    try:
        return serialization.load_pem_public_key(pem.encode('ascii'))
    except ValueError as exc:
        raise PkiError('That is not a PEM public key.') from exc


def same_public_key(a: object, b: object) -> bool:
    try:
        return public_key_bytes(a) == public_key_bytes(b)
    except PkiError:
        return False


# --- subjects ---------------------------------------------------------------


def _name(common_name: str, role: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORG),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, role),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name[:64]),
        ]
    )


def role_of(cert: x509.Certificate) -> str:
    """The role a client certificate was issued for, or ''."""
    units = cert.subject.get_attributes_for_oid(
        NameOID.ORGANIZATIONAL_UNIT_NAME
    )
    role = str(units[0].value) if units else ''
    return role if role in ROLES else ''


# --- building ---------------------------------------------------------------


def new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def make_ca(
    key: ec.EllipticCurvePrivateKey, common_name: str
) -> x509.Certificate:
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORG),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name[:64]),
        ]
    )
    start = now() - CLOCK_SKEW
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + CA_VALIDITY)
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0), critical=True
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )


def make_csr(
    key: ec.EllipticCurvePrivateKey, common_name: str, role: str
) -> str:
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(_name(common_name, role))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode('ascii')


def issue_client_cert(
    ca_cert: x509.Certificate,
    ca_key: ec.EllipticCurvePrivateKey,
    public_key: object,
    common_name: str,
    role: str,
    validity: dt.timedelta = CLIENT_VALIDITY,
) -> x509.Certificate:
    """A client-auth-only certificate for ``public_key``.

    The subject is set here, never copied from a CSR, so a requester
    cannot choose its own role or name.
    """
    if role not in ROLES:
        raise PkiError(f'Unknown certificate role {role!r}.')
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise PkiError(_EC_ONLY)
    start = now() - CLOCK_SKEW
    not_after = min(start + validity, ca_cert.not_valid_after_utc)
    return (
        x509.CertificateBuilder()
        .subject_name(_name(common_name, role))
        .issuer_name(ca_cert.subject)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(not_after)
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_key.public_key()
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )


def make_crl(
    ca_cert: x509.Certificate,
    ca_key: ec.EllipticCurvePrivateKey,
    revoked: Iterable[tuple[int, dt.datetime]],
    number: int,
) -> x509.CertificateRevocationList:
    issued = now()
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_cert.subject)
        .last_update(issued)
        .next_update(issued + CRL_VALIDITY)
        .add_extension(x509.CRLNumber(number), critical=False)
    )
    for serial, when in revoked:
        builder = builder.add_revoked_certificate(
            x509.RevokedCertificateBuilder()
            .serial_number(serial)
            .revocation_date(when)
            .build()
        )
    return builder.sign(ca_key, hashes.SHA256())


def crl_pem(crl: x509.CertificateRevocationList) -> str:
    return crl.public_bytes(serialization.Encoding.PEM).decode('ascii')


# --- checking ---------------------------------------------------------------


def signed_by(
    item: x509.Certificate | x509.CertificateRevocationList,
    ca_cert: x509.Certificate,
) -> bool:
    public = ca_cert.public_key()
    if not isinstance(public, ec.EllipticCurvePublicKey):
        return False
    if isinstance(item, x509.CertificateRevocationList):
        return item.is_signature_valid(public)
    try:
        item.verify_directly_issued_by(ca_cert)
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def check_ca(ca_cert: x509.Certificate) -> None:
    try:
        constraints = ca_cert.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value
    except x509.ExtensionNotFound as exc:
        raise PkiError('That certificate is not a CA.') from exc
    if not constraints.ca or not signed_by(ca_cert, ca_cert):
        raise PkiError('That certificate is not a self-signed CA.')


def check_client_cert(
    cert: x509.Certificate,
    ca_cert: x509.Certificate,
    role: str,
    public_key: object | None = None,
    at: dt.datetime | None = None,
) -> None:
    """Raise PkiError unless ``cert`` is a valid ``role`` cert from ``ca``."""
    when = at or now()
    if not signed_by(cert, ca_cert):
        raise PkiError('The certificate was not issued by this CA.')
    if role_of(cert) != role:
        raise PkiError(f'The certificate is not a {role} certificate.')
    if not cert.not_valid_before_utc <= when <= cert.not_valid_after_utc:
        raise PkiError('The certificate is expired or not yet valid.')
    if public_key is not None and not same_public_key(
        cert.public_key(), public_key
    ):
        raise PkiError('The certificate is not for this device’s key.')


def check_crl(
    crl: x509.CertificateRevocationList, ca_cert: x509.Certificate
) -> None:
    if not signed_by(crl, ca_cert):
        raise PkiError('The revocation list is not signed by this CA.')


def revoked_serials(crl: x509.CertificateRevocationList) -> frozenset[int]:
    return frozenset(entry.serial_number for entry in crl)


def crl_number(crl: x509.CertificateRevocationList) -> int:
    try:
        return int(
            crl.extensions.get_extension_for_class(
                x509.CRLNumber
            ).value.crl_number
        )
    except x509.ExtensionNotFound:
        return 0


def needs_renewal(
    cert: x509.Certificate, at: dt.datetime | None = None
) -> bool:
    return cert.not_valid_after_utc - (at or now()) < RENEW_BEFORE


def check_code(node_fingerprint: str) -> str:
    """What the operator compares on screen and controller: ABCD-1234."""
    head = node_fingerprint[:8].upper()
    return f'{head[:4]}-{head[4:]}'


def random_request_id() -> str:
    return secrets.token_urlsafe(24)
