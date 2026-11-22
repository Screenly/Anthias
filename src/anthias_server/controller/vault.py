"""Encrypt the API tokens screens issue to this controller.

Tokens are stored in the database encrypted with Fernet (AES-128-CBC
plus HMAC-SHA256). The key lives in its own file,
``~/.anthias/controller.key``, created with mode 0600 on first use.

The backup archive is a whitelist that ships only the settings file,
the database and the asset store (see ``lib/backup_helper``), so the
key is never in a backup. A backup leaked or restored onto another
device therefore carries no usable screen tokens: those screens show
"Needs pairing again" there. That is deliberate. Restoring a
controller's backup must not clone its access to every screen.
"""

from __future__ import annotations

import os
from functools import cache

from cryptography.fernet import Fernet, InvalidToken

KEY_FILENAME = 'controller.key'


class VaultError(Exception):
    """A stored token cannot be decrypted with this device's key."""


def key_path() -> str:
    from anthias_server.settings import settings

    return os.path.join(settings.get_configdir(), KEY_FILENAME)


def _read_or_create_key(path: str) -> bytes:
    try:
        with open(path, 'rb') as f:
            return f.read().strip()
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    key = Fernet.generate_key()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process won the race; use its key.
        with open(path, 'rb') as f:
            return f.read().strip()
    with os.fdopen(fd, 'wb') as f:
        f.write(key)
    return key


@cache
def _fernet_for(path: str) -> Fernet:
    return Fernet(_read_or_create_key(path))


def _fernet() -> Fernet:
    return _fernet_for(key_path())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode('utf-8')).decode('ascii')


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError) as exc:
        raise VaultError(
            'The stored token cannot be read on this device. '
            'Pair the screen again.'
        ) from exc


def reset_cache() -> None:
    """Forget the cached key (tests, or after the key file changes)."""
    _fernet_for.cache_clear()
