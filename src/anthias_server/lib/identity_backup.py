"""This device's keys in a backup: opt-in, and always encrypted.

A normal backup never contains a private key (``backup_helper`` ships
an allowlist of runtime files). That keeps a copied backup from
cloning a device, but it also means a multi-screen controller cannot
be moved to new hardware: its screens pin its node certificate, trust
only its CA, and their tokens are encrypted with its vault key, so
every screen has to join again.

So, like pfSense's and OPNsense's configuration backups (whose single
config file carries the CA and certificate private keys, protected by
an "Encryption" password), Settings can add the keys to a backup when
the operator ticks the box and sets a password. Unlike there, the
password is not optional: the keys never leave the device unencrypted.

What is included (whatever of it exists on this device):

* the node certificate and key, so peers' fingerprint pins still match,
  plus a managed screen's client certificate, controller CA and CRL;
* a controller's CA certificate and key, its own client certificate and
  its CRL (``~/.anthias/ssl/controller/``);
* the vault key that encrypts a controller's join secret and screen
  tokens.

The bundle is one archive member, :data:`MEMBER`: ``MAGIC``, a random
salt and nonce, then AES-256-GCM over a small JSON document, with the
key derived from the password by scrypt. A wrong password fails the
GCM tag check, so nothing is ever restored from a bad guess.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

logger = logging.getLogger(__name__)

MEMBER = 'anthias-device-keys.enc'
MAGIC = b'ANTHIAS-KEYS-1\n'
MIN_PASSWORD_LEN = 8
MAX_BUNDLE_BYTES = 1024 * 1024
_SALT_BYTES = 16
_NONCE_BYTES = 12
# scrypt work factor: about 0.1 s and 32 MiB on a Pi 4, enough to make
# guessing slow without making a backup or restore noticeably slower.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_FORMAT = 1

# Bundle names → where they live. Private keys are written 0600.
_NODE_FILES = (
    'cert.pem',
    'key.pem',
    'managed-client.crt',
    'managed-ca.crt',
    'managed-crl.pem',
)
_CONTROLLER_FILES = ('ca.crt', 'ca.key', 'client.crt', 'crl.pem')
_PRIVATE = frozenset({'node/key.pem', 'controller/ca.key', 'vault.key'})


class IdentityBackupError(Exception):
    """The keys could not be packed, or the bundle cannot be opened."""


def _locations() -> dict[str, Path]:
    from anthias_server.controller import ca, vault
    from anthias_server.lib import node_identity

    places = {f'node/{n}': node_identity.node_dir() / n for n in _NODE_FILES}
    places.update(
        {f'controller/{n}': ca.ca_dir() / n for n in _CONTROLLER_FILES}
    )
    places['vault.key'] = Path(vault.key_path())
    return places


def check_password(password: str, confirm: str | None = None) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise IdentityBackupError(
            f'The password must be at least {MIN_PASSWORD_LEN} characters.'
        )
    if confirm is not None and password != confirm:
        raise IdentityBackupError('The two passwords do not match.')


def _key(password: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return kdf.derive(password.encode('utf-8'))


def pack(password: str) -> bytes:
    """This device's keys, encrypted with ``password``."""
    from anthias_server.lib import node_identity

    check_password(password)
    # Make sure there is a node identity to back up.
    node_identity.ensure_node_identity()
    files: dict[str, str] = {}
    for name, location in _locations().items():
        try:
            files[name] = base64.b64encode(location.read_bytes()).decode()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise IdentityBackupError(f'Cannot read {name}: {exc}') from exc
    document = json.dumps({'format': _FORMAT, 'files': files}).encode()
    salt = secrets.token_bytes(_SALT_BYTES)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    sealed = AESGCM(_key(password, salt)).encrypt(nonce, document, MAGIC)
    return MAGIC + salt + nonce + sealed


def pack_checked(password: str, confirm: str) -> bytes:
    """:func:`pack`, after checking the password and its confirmation."""
    check_password(password, confirm)
    return pack(password)


def unpack(blob: bytes, password: str) -> dict[str, bytes]:
    """The files in a bundle. Raises IdentityBackupError if it can't."""
    head = len(MAGIC) + _SALT_BYTES + _NONCE_BYTES
    if len(blob) > MAX_BUNDLE_BYTES or not blob.startswith(MAGIC):
        raise IdentityBackupError(
            'The device keys in this backup are unreadable.'
        )
    if len(blob) <= head:
        raise IdentityBackupError(
            'The device keys in this backup are unreadable.'
        )
    salt = blob[len(MAGIC) : len(MAGIC) + _SALT_BYTES]
    nonce = blob[len(MAGIC) + _SALT_BYTES : head]
    try:
        document = AESGCM(_key(password, salt)).decrypt(
            nonce, blob[head:], MAGIC
        )
    except InvalidTag as exc:
        raise IdentityBackupError(
            'Wrong password for the device keys in this backup.'
        ) from exc
    try:
        data: Any = json.loads(document)
        files = {
            str(name): base64.b64decode(value)
            for name, value in data['files'].items()
        }
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise IdentityBackupError(
            'The device keys in this backup are unreadable.'
        ) from exc
    known = _locations()
    unknown = set(files) - set(known)
    if unknown:
        raise IdentityBackupError(
            f'Unexpected files in the device keys: {sorted(unknown)}'
        )
    return files


def _write(target: Path, data: bytes, private: bool) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if private:
        os.chmod(target.parent, 0o700)
    tmp = target.with_name(f'.{target.name}.restore')
    mode = 0o600 if private else 0o644
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    # Certificates are public; keys are 0600.
    os.chmod(tmp, mode)  # NOSONAR(S2612)
    os.replace(tmp, target)


def install(files: dict[str, bytes]) -> list[str]:
    """Put restored keys in place. Returns the names written.

    A set that is only partly present replaces only what it carries:
    the files of the other role (screen / controller) are left alone.
    Restoring the node identity removes the derived files of the old
    one that the bundle does not replace, so nothing mixes two
    identities.
    """
    from anthias_server.controller import vault

    locations = _locations()
    written = []
    for name, data in sorted(files.items()):
        _write(locations[name], data, name in _PRIVATE)
        written.append(name)
    if 'node/key.pem' in files:
        for name in _NODE_FILES:
            bundle_name = f'node/{name}'
            if bundle_name not in files:
                locations[bundle_name].unlink(missing_ok=True)
    if 'controller/ca.key' in files:
        for name in ('client.crt', 'crl.pem'):
            bundle_name = f'controller/{name}'
            if bundle_name not in files:
                locations[bundle_name].unlink(missing_ok=True)
    vault.reset_cache()
    logger.info('Restored device keys: %s', ', '.join(written))
    return written


def describe(files: dict[str, bytes]) -> str:
    """A short human summary of what a bundle holds."""
    parts = []
    if 'node/key.pem' in files:
        parts.append("this device's certificate")
    if 'controller/ca.key' in files:
        parts.append('the multi-screen CA')
    if 'node/managed-client.crt' in files:
        parts.append('its multi-screen client certificate')
    return ', '.join(parts) or 'no keys'
