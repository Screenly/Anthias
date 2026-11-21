"""Device keys in a backup: opt-in, encrypted, all-or-nothing on restore."""

from __future__ import annotations

import os
import shutil
import stat
import tarfile
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from anthias_server.controller import ca, vault
from anthias_server.lib import backup_helper, identity_backup, node_identity

PASSWORD = 'correct horse battery'  # NOSONAR: test fixture


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = Path(tempfile.mkdtemp(prefix='anthias-keys-test-'))
    (root / '.anthias').mkdir()
    (root / 'anthias_assets').mkdir()
    (root / '.anthias' / 'anthias.conf').write_text('[viewer]\n')
    monkeypatch.setenv('HOME', str(root))
    monkeypatch.setattr(
        vault, 'key_path', lambda: str(root / '.anthias' / 'vault.key')
    )
    vault.reset_cache()
    yield root
    vault.reset_cache()
    shutil.rmtree(root, ignore_errors=True)


def _controller_identity() -> dict[str, bytes]:
    """A node identity, a CA and a vault key, as a controller has."""
    node_identity.ensure_node_identity()
    ca.ensure_ca()
    vault.encrypt('anything')  # creates the vault key
    return {
        name: location.read_bytes()
        for name, location in identity_backup._locations().items()
        if location.exists()
    }


def _archive(home: Path, device_keys: bytes | None) -> str:
    target = home / 'backup.tar.gz'
    target.write_bytes(b''.join(backup_helper.stream_backup(device_keys)))
    return str(target)


def _wipe(files: dict[str, bytes]) -> None:
    locations = identity_backup._locations()
    for name in files:
        locations[name].unlink()


# --- the bundle -------------------------------------------------------------


@pytest.mark.django_db
def test_bundle_round_trips_every_key(home: Path) -> None:
    files = _controller_identity()
    assert {'node/key.pem', 'controller/ca.key', 'vault.key'} <= set(files)
    blob = identity_backup.pack(PASSWORD)
    assert blob.startswith(identity_backup.MAGIC)
    # Nothing readable in the clear.
    for data in files.values():
        assert data not in blob
    assert identity_backup.unpack(blob, PASSWORD) == files
    assert 'the multi-screen CA' in identity_backup.describe(files)


@pytest.mark.django_db
def test_bundle_refuses_a_wrong_password_or_tampering(home: Path) -> None:
    _controller_identity()
    blob = identity_backup.pack(PASSWORD)
    with pytest.raises(identity_backup.IdentityBackupError, match='Wrong'):
        identity_backup.unpack(blob, PASSWORD + 'x')
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(identity_backup.IdentityBackupError):
        identity_backup.unpack(tampered, PASSWORD)
    with pytest.raises(identity_backup.IdentityBackupError):
        identity_backup.unpack(b'not a bundle', PASSWORD)


@pytest.mark.parametrize(
    ('password', 'confirm'),
    [('short', 'short'), (PASSWORD, PASSWORD + '!')],
)
def test_bundle_needs_a_good_confirmed_password(
    password: str, confirm: str
) -> None:
    with pytest.raises(identity_backup.IdentityBackupError):
        identity_backup.pack_checked(password, confirm)


# --- in a backup ------------------------------------------------------------


@pytest.mark.django_db
def test_a_plain_backup_still_has_no_keys(home: Path) -> None:
    files = _controller_identity()
    archive = _archive(home, None)
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert identity_backup.MEMBER not in names
    for data in files.values():
        assert data not in Path(archive).read_bytes()
    assert backup_helper.recover(archive) == backup_helper.KeysRestore.NONE


@pytest.mark.django_db
def test_keys_come_back_with_the_password(home: Path) -> None:
    files = _controller_identity()
    archive = _archive(home, identity_backup.pack(PASSWORD))
    _wipe(files)

    state = backup_helper.recover(archive, PASSWORD)

    assert state == backup_helper.KeysRestore.RESTORED
    locations = identity_backup._locations()
    for name, data in files.items():
        assert locations[name].read_bytes() == data, name
    for name in ('node/key.pem', 'controller/ca.key', 'vault.key'):
        mode = stat.S_IMODE(locations[name].stat().st_mode)
        assert mode == 0o600, name
    # The token key works again: what it encrypted can be read.
    assert vault.decrypt(vault.encrypt('x')) == 'x'
    # Never written to disk as a file of its own.
    assert not (home / identity_backup.MEMBER).exists()


@pytest.mark.django_db
def test_without_the_password_the_rest_is_restored(home: Path) -> None:
    files = _controller_identity()
    archive = _archive(home, identity_backup.pack(PASSWORD))
    _wipe(files)
    conf = home / '.anthias' / 'anthias.conf'
    conf.unlink()

    assert backup_helper.recover(archive) == backup_helper.KeysRestore.SKIPPED
    assert conf.exists()
    assert not identity_backup._locations()['controller/ca.key'].exists()


@pytest.mark.django_db
def test_a_wrong_password_restores_nothing(home: Path) -> None:
    files = _controller_identity()
    archive = _archive(home, identity_backup.pack(PASSWORD))
    _wipe(files)
    conf = home / '.anthias' / 'anthias.conf'
    conf.write_text('[changed]\n')

    with pytest.raises(backup_helper.DeviceKeysError):
        backup_helper.recover(archive, 'wrong password')

    assert conf.read_text() == '[changed]\n'
    assert not identity_backup._locations()['node/key.pem'].exists()


# --- Settings ---------------------------------------------------------------


@pytest.fixture
def operator_client() -> Client:
    user = User.objects.create_superuser(
        username='operator',
        password='op-pass-1234',  # NOSONAR
    )
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_settings_refuses_keys_without_a_confirmed_password(
    home: Path, operator_client: Client
) -> None:
    response = operator_client.post(
        reverse('anthias_app:settings_backup'),
        {
            'include_keys': 'true',
            'keys_password': PASSWORD,
            'keys_password_2': 'something else',
        },
    )
    assert response.status_code == 302
    assert response['Location'].endswith('#backup')


@pytest.mark.django_db
def test_settings_offers_the_keys_option(
    home: Path, operator_client: Client
) -> None:
    html = operator_client.get(reverse('anthias_app:settings')).content
    assert b'id="include_keys"' in html
    assert b'id="recover_keys_password"' in html


@pytest.mark.django_db
def test_settings_recover_reports_skipped_keys(
    home: Path, operator_client: Client
) -> None:
    _controller_identity()
    archive = Path(_archive(home, identity_backup.pack(PASSWORD)))
    os.makedirs('static', exist_ok=True)
    with (
        mock.patch('anthias_server.app.views.ViewerPublisher.get_instance'),
        archive.open('rb') as upload,
    ):
        response = operator_client.post(
            reverse('anthias_app:settings_recover'),
            {'backup_upload': upload},
            follow=True,
        )
    text = response.content.decode()
    assert 'were not restored' in text


@pytest.mark.django_db
def test_restore_asks_before_replacing_anything(
    home: Path, operator_client: Client
) -> None:
    html = operator_client.get(reverse('anthias_app:settings')).content
    text = html.decode()
    # Picking a file only opens the confirmation step.
    assert 'id="btn-restore-confirm"' in text
    assert 'id="btn-restore-cancel"' in text
    form = text.split('id="recover-form"')[1].split('</form>')[0]
    assert 'name="keys_password"' in form
    assert 'requestSubmit' not in text.split('id="backup"')[1]
