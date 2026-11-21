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

import logging
import os
import tempfile
from functools import cache

from cryptography.fernet import Fernet, InvalidToken

KEY_FILENAME = 'controller.key'

logger = logging.getLogger(__name__)


class VaultError(Exception):
    """A stored token cannot be decrypted with this device's key."""


def key_path() -> str:
    from anthias_server.settings import settings

    return os.path.join(settings.get_configdir(), KEY_FILENAME)


def _usable(key: bytes) -> bool:
    try:
        Fernet(key)
    except ValueError:
        return False
    return True


def _write_new_key(path: str, *, replace: bool) -> bytes:
    """Write a fresh key durably; readers never see a partial file.

    The key goes to a private temp file, is fsynced, and only then
    appears under its real name: ``os.link`` when creating, so a
    concurrent creator loses cleanly, or ``os.replace`` when an
    unusable key is being replaced.
    """
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    key = Fernet.generate_key()
    fd, tmp = tempfile.mkstemp(prefix='.controller-key-', dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(key)
            f.flush()
            os.fsync(f.fileno())
        if replace:
            os.replace(tmp, path)
        else:
            os.link(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return key


def _read_or_create_key(path: str) -> bytes:
    for _ in range(3):
        try:
            with open(path, 'rb') as f:
                key = f.read().strip()
        except FileNotFoundError:
            try:
                return _write_new_key(path, replace=False)
            except FileExistsError:
                # Another process created it first; use that key.
                continue
        if _usable(key):
            return key
        # Empty or damaged (e.g. power lost before an older version
        # flushed it). Nothing encrypted with it can be read anyway,
        # so replacing it costs nothing that is not already lost; the
        # affected screens show "Needs pairing again".
        logger.warning('Replacing an unusable controller key at %s', path)
        return _write_new_key(path, replace=True)
    raise VaultError('The controller key could not be created.')


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
