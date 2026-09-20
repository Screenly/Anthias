import asyncio
import io
import json
import os
import shutil
import tarfile
import tempfile
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from os import path
from pathlib import Path
from unittest import mock

import pytest
from django.http import StreamingHttpResponse

from anthias_common.utils import STAGED_UPLOAD_DIR
from anthias_server.lib.backup_helper import (
    BACKUP_MANIFEST_NAME,
    BackupRecoverError,
    IncompatibleBackupError,
    _current_schema_version,
    astream_backup,
    backup_archive_name,
    create_backup,
    recover,
    static_dir,
    stream_backup,
)


@pytest.fixture
def backup_home() -> Iterator[str]:
    """Exercises create_backup() / recover() under a temporary $HOME so a
    developer running the test on a real workstation never has their
    ~/anthias checkout or ~/.anthias config wiped by tearDown's
    rmtree."""
    tmp_home = tempfile.mkdtemp(prefix='anthias-backup-test-')
    # Populate the layout create_backup() expects to tar up so the
    # call has something to read.
    os.makedirs(path.join(tmp_home, '.anthias'))
    os.makedirs(path.join(tmp_home, 'anthias_assets'))

    home_patch = mock.patch.dict(os.environ, {'HOME': tmp_home})
    home_patch.start()

    assert not path.isdir(path.join(tmp_home, static_dir))

    try:
        yield tmp_home
    finally:
        home_patch.stop()
        shutil.rmtree(tmp_home, ignore_errors=True)


def test_get_backup_name(backup_home: str) -> None:
    dt = datetime(2016, 7, 19, 12, 42, 12, tzinfo=UTC)
    expected_archive_name = 'anthias-backup-2016-07-19T12-42-12.tar.gz'
    with mock.patch(
        'anthias_server.lib.backup_helper.datetime'
    ) as mock_datetime:
        mock_datetime.now.return_value = dt
        archive_name = create_backup()
        assert archive_name == expected_archive_name


def test_recover(backup_home: str) -> None:
    archive_name = create_backup()
    file_path = path.join(backup_home, static_dir, archive_name)
    assert path.isfile(file_path)
    recover(file_path)
    assert not path.isfile(file_path)


def test_backup_archive_name_falls_back_on_empty_name() -> None:
    dt = datetime(2016, 7, 19, 12, 42, 12, tzinfo=UTC)
    with mock.patch(
        'anthias_server.lib.backup_helper.datetime'
    ) as mock_datetime:
        mock_datetime.now.return_value = dt
        assert (
            backup_archive_name('')
            == 'anthias-backup-2016-07-19T12-42-12.tar.gz'
        )
        assert (
            backup_archive_name('lobby') == 'lobby-2016-07-19T12-42-12.tar.gz'
        )


def test_stream_backup_round_trips_through_recover(
    backup_home: str,
) -> None:
    # The settings page download streams the archive as it is built
    # (issue #2987: the staged-file path produced no response bytes
    # for minutes and browsers gave up). The streamed bytes must be a
    # well-formed tar.gz that recover() accepts unchanged.
    marker = path.join(backup_home, '.anthias', 'anthias.conf')
    with open(marker, 'w') as f:
        f.write('[viewer]\n')

    chunks = list(stream_backup())
    assert chunks

    os.makedirs(path.join(backup_home, static_dir), exist_ok=True)
    file_path = path.join(backup_home, static_dir, 'streamed.tar.gz')
    with open(file_path, 'wb') as f:
        f.write(b''.join(chunks))

    with tarfile.open(file_path, 'r:gz') as tar:
        names = tar.getnames()
    assert '.anthias' in names
    assert 'anthias_assets' in names
    assert '.anthias/anthias.conf' in names

    os.remove(marker)
    recover(file_path)
    assert path.isfile(marker)


def test_stream_backup_stops_when_consumer_disconnects(
    backup_home: str,
) -> None:
    # A closed browser connection must not leave the producer thread
    # taring forever — the generator's pipe close propagates as
    # BrokenPipeError and the thread exits.
    stream = stream_backup()
    assert next(stream)
    stream.close()  # GeneratorExit → read end closed
    main_thread = threading.main_thread()
    for thread in threading.enumerate():
        if thread.name == 'backup-stream' and thread is not main_thread:
            thread.join(timeout=5)
            assert not thread.is_alive()


def test_astream_backup_response_streams_under_asgi(
    backup_home: str,
) -> None:
    # Regression for issue #3073. StreamingHttpResponse only streams an
    # *asynchronous* iterator under ASGI; handed a sync generator,
    # Django's __aiter__ does `await sync_to_async(list)(...)` — it
    # builds the whole archive in RAM before the first byte, which
    # reproduced the original 0-bytes-then-timeout failure. The download
    # view must wrap the producer in astream_backup() so Django takes
    # its real streaming path (is_async == True) and round-trips back
    # through recover() unchanged.
    marker = path.join(backup_home, '.anthias', 'anthias.conf')
    with open(marker, 'w') as f:
        f.write('[viewer]\n')

    response = StreamingHttpResponse(
        astream_backup(), content_type='application/x-tgz'
    )
    # The crux of the fix: a sync generator would leave this False and
    # send Django down the list()-buffering branch.
    assert response.is_async is True

    async def drain() -> list[bytes]:
        # aiter(response) is exactly what Django's ASGI handler consumes.
        return [part async for part in aiter(response)]

    chunks = asyncio.run(drain())
    assert chunks

    os.makedirs(path.join(backup_home, static_dir), exist_ok=True)
    file_path = path.join(backup_home, static_dir, 'astreamed.tar.gz')
    with open(file_path, 'wb') as out_file:
        out_file.write(b''.join(chunks))

    with tarfile.open(file_path, 'r:gz') as tar:
        names = tar.getnames()
    assert '.anthias/anthias.conf' in names

    os.remove(marker)
    recover(file_path)
    assert path.isfile(marker)


def test_astream_backup_stops_producer_when_consumer_disconnects(
    backup_home: str,
) -> None:
    # A client that disconnects mid-download makes Django aclose() the
    # async generator. Cleanup must stop the producer thread (and not
    # raise) — a cross-thread close racing an in-flight next() would
    # leave it taring forever (PR #3074 review).
    marker = path.join(backup_home, '.anthias', 'anthias.conf')
    with open(marker, 'w') as f:
        f.write('[viewer]\n')

    async def take_one_then_disconnect() -> None:
        agen = astream_backup()
        first = await agen.__anext__()
        assert first
        await agen.aclose()  # GeneratorExit cleanup path

    asyncio.run(take_one_then_disconnect())

    main_thread = threading.main_thread()
    for thread in threading.enumerate():
        if thread.name == 'backup-stream' and thread is not main_thread:
            thread.join(timeout=5)
            assert not thread.is_alive()


@pytest.fixture
def legacy_home() -> Iterator[str]:
    """Backups produced by pre-rename releases used `.screenly` and
    `screenly_assets` as top-level archive entries. recover() must keep
    accepting them so users can still restore those backups."""
    tmp_home = tempfile.mkdtemp(prefix='anthias-backup-legacy-test-')
    try:
        yield tmp_home
    finally:
        shutil.rmtree(tmp_home, ignore_errors=True)


def _build_legacy_tarball(tmp_home: str) -> str:
    # Stage the legacy layout in a scratch dir, then tar it up with
    # top-level `.screenly/` and `screenly_assets/` arcnames.
    scratch = tempfile.mkdtemp(prefix='anthias-backup-stage-')
    try:
        os.makedirs(path.join(scratch, '.screenly'))
        os.makedirs(path.join(scratch, 'screenly_assets'))
        with open(path.join(scratch, '.screenly', 'screenly.conf'), 'w') as f:
            f.write('[main]\nconfigdir = .screenly\n')
        with open(path.join(scratch, 'screenly_assets', 'a.mp4'), 'wb') as f:
            f.write(b'video-stub')

        archive = path.join(tmp_home, 'legacy-backup.tar.gz')
        # Write mode: building a fixture tarball, not extracting it.
        # arcnames are hardcoded test inputs, so no path-traversal
        # surface. NOSONAR(python:S5042)
        with tarfile.open(archive, 'w:gz') as tar:  # NOSONAR
            tar.add(
                path.join(scratch, '.screenly'),
                arcname='.screenly',
            )
            tar.add(
                path.join(scratch, 'screenly_assets'),
                arcname='screenly_assets',
            )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return archive


def test_recover_accepts_legacy_archive(legacy_home: str) -> None:
    archive = _build_legacy_tarball(legacy_home)

    with mock.patch.dict(os.environ, {'HOME': legacy_home}):
        recover(archive)

    # Archive removed (recover() unlinks on success).
    assert not path.isfile(archive)
    # Legacy entries restored under the patched HOME.
    assert path.isfile(path.join(legacy_home, '.screenly', 'screenly.conf'))
    assert path.isfile(path.join(legacy_home, 'screenly_assets', 'a.mp4'))


def test_recover_rejects_unrelated_archive(legacy_home: str) -> None:
    archive = path.join(legacy_home, 'random.tar.gz')
    scratch = tempfile.mkdtemp(prefix='anthias-backup-bogus-')
    try:
        os.makedirs(path.join(scratch, 'unrelated'))
        # Write mode: building a fixture tarball, not extracting it.
        # NOSONAR(python:S5042)
        with tarfile.open(archive, 'w:gz') as tar:  # NOSONAR
            tar.add(
                path.join(scratch, 'unrelated'),
                arcname='unrelated',
            )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    with (
        mock.patch.dict(os.environ, {'HOME': legacy_home}),
        pytest.raises(BackupRecoverError),
    ):
        recover(archive)


def test_recover_skips_path_traversal_member(legacy_home: str) -> None:
    """A malicious tarball with a `..` member must not write outside
    $HOME. The required top-level entries are still present, so
    recover() proceeds, but the unsafe member should be skipped."""
    archive = path.join(legacy_home, 'malicious.tar.gz')
    scratch = tempfile.mkdtemp(prefix='anthias-backup-mal-')
    try:
        os.makedirs(path.join(scratch, '.anthias'))
        os.makedirs(path.join(scratch, 'anthias_assets'))
        with open(path.join(scratch, '.anthias', 'anthias.conf'), 'w') as f:
            f.write('[main]\n')
        payload = path.join(scratch, 'evil.txt')
        with open(payload, 'wb') as f:
            f.write(b'pwned')

        # NOSONAR(python:S5042) — fixture builder, write mode.
        with tarfile.open(archive, 'w:gz') as tar:  # NOSONAR
            tar.add(path.join(scratch, '.anthias'), arcname='.anthias')
            tar.add(
                path.join(scratch, 'anthias_assets'),
                arcname='anthias_assets',
            )
            # The hostile member: a relative escape attempt that
            # would land at $HOME/../evil.txt under naive extraction.
            tar.add(payload, arcname='../evil.txt')
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    with mock.patch.dict(os.environ, {'HOME': legacy_home}):
        recover(archive)

    # Legit member extracted; hostile one skipped.
    assert path.isfile(path.join(legacy_home, '.anthias', 'anthias.conf'))
    parent_of_home = path.dirname(legacy_home)
    assert not path.exists(path.join(parent_of_home, 'evil.txt'))


@pytest.mark.parametrize(
    'relative',
    [
        f'anthias_assets/{STAGED_UPLOAD_DIR}/abc.part',
        'anthias_assets/deadbeef.tmp',
        'anthias_assets/.import-deadbeef.mp4',
        'anthias_assets/.import-deadbeef.mp4.part',
    ],
)
def test_backup_excludes_half_finished_uploads(
    backup_home: str, relative: str
) -> None:
    """A backup taken mid-upload would otherwise carry gigabytes of a
    file nobody finished sending, and that is meaningless once
    restored — the session writing it is gone."""
    staged = path.join(backup_home, relative)
    os.makedirs(path.dirname(staged), exist_ok=True)
    with open(staged, 'wb') as f:
        f.write(b'0' * 1024)
    keep = path.join(backup_home, 'anthias_assets', 'deadbeef.mp4')
    with open(keep, 'wb') as f:
        f.write(b'1' * 16)

    archive = create_backup(name='exclusion-test')
    with tarfile.open(path.join(backup_home, static_dir, archive)) as tar:
        names = tar.getnames()

    assert not [n for n in names if n.endswith(path.basename(staged))]
    assert [n for n in names if n.endswith('deadbeef.mp4')]


# Backup/restore work off a whitelist of runtime state (settings, the
# metadata DB, the default-asset manifest, db backups, and media
# assets). A backup ships only those; a restore writes only those.
# Everything else — SSH keys, SSL keys, logs, a Caddyfile, a file a
# crafted archive invents — is not runtime state and is refused, without
# enumerating specific dangerous names.

_OWNER_KEY = b'ssh-ed25519 AAAAOWNER owner@device\n'
_ATTACKER_KEY = b'ssh-ed25519 AAAAATTACKER attacker@evil\n'


def _stage_legit_layout(tar: tarfile.TarFile) -> None:
    """Add the two required top-level entries so recover() clears its
    "is this actually a backup?" check and goes on to process the
    member under test."""
    for directory in ('.anthias', 'anthias_assets'):
        info = tarfile.TarInfo(directory)
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        tar.addfile(info)
    body = b'[main]\n'
    conf = tarfile.TarInfo('.anthias/anthias.conf')
    conf.size = len(body)
    tar.addfile(conf, io.BytesIO(body))


def _add_file_member(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _seed_ssh_key(home: str) -> str:
    ssh_dir = path.join(home, '.ssh')
    os.makedirs(ssh_dir)
    keyfile = path.join(ssh_dir, 'authorized_keys')
    with open(keyfile, 'wb') as f:
        f.write(_OWNER_KEY)
    return keyfile


@pytest.mark.parametrize(
    'hostile_name',
    [
        '.ssh/authorized_keys',  # top-level dir, not runtime
        '../.ssh/authorized_keys',  # parent-of-HOME traversal
        '.anthias/../.ssh/authorized_keys',  # traversal via runtime prefix
    ],
)
def test_recover_cannot_plant_ssh_key(
    backup_home: str, hostile_name: str
) -> None:
    """A crafted restore upload must not land a key in
    ~/.ssh/authorized_keys. The member falls outside the runtime
    whitelist (and trips the traversal guard), so it is skipped and a
    pre-existing authorized_keys is left untouched."""
    keyfile = _seed_ssh_key(backup_home)

    archive = path.join(backup_home, 'malicious.tar.gz')
    # NOSONAR(python:S5042) — fixture builder, write mode.
    with tarfile.open(archive, 'w:gz') as tar:  # NOSONAR
        _stage_legit_layout(tar)
        _add_file_member(tar, hostile_name, _ATTACKER_KEY)

    recover(archive)  # backup_home already patches $HOME

    # Runtime member restored (recover() ran); attacker key never written.
    assert path.isfile(path.join(backup_home, '.anthias', 'anthias.conf'))
    with open(keyfile, 'rb') as f:
        assert f.read() == _OWNER_KEY


def test_recover_blocks_absolute_path_member(backup_home: str) -> None:
    """An absolute-path member aimed straight at ~/.ssh/authorized_keys
    is rejected before any write."""
    keyfile = _seed_ssh_key(backup_home)

    archive = path.join(backup_home, 'absolute.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:  # NOSONAR
        _stage_legit_layout(tar)
        _add_file_member(tar, keyfile, _ATTACKER_KEY)  # arcname is absolute

    recover(archive)

    with open(keyfile, 'rb') as f:
        assert f.read() == _OWNER_KEY


def test_recover_blocks_symlink_escape(backup_home: str) -> None:
    """The classic tar symlink escape: a symlink member pointing out of
    the extraction root, then a file that writes *through* it.
    _safe_tar_member() rejects link members outright, so the symlink is
    never created and the real key is untouched."""
    keyfile = _seed_ssh_key(backup_home)

    archive = path.join(backup_home, 'symlink.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:  # NOSONAR
        _stage_legit_layout(tar)
        link = tarfile.TarInfo('.anthias/sshlink')
        link.type = tarfile.SYMTYPE
        link.linkname = '../.ssh'
        tar.addfile(link)
        _add_file_member(
            tar, '.anthias/sshlink/authorized_keys', _ATTACKER_KEY
        )

    recover(archive)

    assert not path.islink(path.join(backup_home, '.anthias', 'sshlink'))
    with open(keyfile, 'rb') as f:
        assert f.read() == _OWNER_KEY


def test_recover_writes_only_runtime_files(backup_home: str) -> None:
    """Restore extracts the runtime whitelist (settings, DB, db backups,
    assets) and skips everything else under the config dir — a stray
    key, an SSL key, a shell script — even though it sits below an
    allowed top-level directory."""
    archive = path.join(backup_home, 'mixed.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:  # NOSONAR
        _stage_legit_layout(tar)
        # Runtime members that must be restored.
        _add_file_member(tar, '.anthias/anthias.db', b'DBDATA')
        _add_file_member(tar, '.anthias/backups/dump.sqlite3', b'DUMP')
        _add_file_member(tar, 'anthias_assets/pic.jpg', b'JPEG')
        # Non-runtime members that must be skipped.
        _add_file_member(tar, '.anthias/authorized_keys', _ATTACKER_KEY)
        _add_file_member(tar, '.anthias/ssl/key.pem', b'PRIVKEY')
        _add_file_member(tar, '.anthias/evil.sh', b'#!/bin/sh\n')

    recover(archive)

    a = path.join(backup_home, '.anthias')
    assert path.isfile(path.join(a, 'anthias.conf'))
    assert path.isfile(path.join(a, 'anthias.db'))
    assert path.isfile(path.join(a, 'backups', 'dump.sqlite3'))
    assert path.isfile(path.join(backup_home, 'anthias_assets', 'pic.jpg'))
    assert not path.exists(path.join(a, 'authorized_keys'))
    assert not path.exists(path.join(a, 'ssl'))
    assert not path.exists(path.join(a, 'evil.sh'))


def test_backup_ships_only_runtime_files(backup_home: str) -> None:
    """A backup captures the runtime whitelist and nothing else. Config,
    DB (+ sidecars), default-asset manifest, db backups and assets are
    kept; an SSL private key, the playback-stats log, an SSH key an
    operator tucked under ~/.anthias, and a stray file are all dropped."""
    a = path.join(backup_home, '.anthias')
    kept = {
        path.join(a, 'anthias.conf'): b'[main]\n',
        path.join(a, 'anthias.db'): b'DB',
        path.join(a, 'anthias.db-wal'): b'WAL',
        path.join(a, 'default_assets.yml'): b'assets: []\n',
        path.join(a, 'backups', 'dump.sqlite3'): b'DUMP',
        path.join(a, 'backups', 'dump.sqlite3.metadata'): b'{}',
        path.join(backup_home, 'anthias_assets', 'clip.mp4'): b'VIDEO',
    }
    dropped = {
        path.join(a, 'ssl', 'key.pem'): b'PRIVKEY',
        path.join(a, 'playback-stats.log'): b'log',
        path.join(a, 'id_ed25519'): b'PRIVKEY',
        path.join(a, 'anthias.conf.tmp'): b'sidecar',
    }
    for p, data in {**kept, **dropped}.items():
        os.makedirs(path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f:
            f.write(data)

    archive = create_backup(name='runtime-scope')
    with tarfile.open(path.join(backup_home, static_dir, archive)) as tar:
        names = set(tar.getnames())

    for p in kept:
        rel = path.relpath(p, backup_home)
        assert rel in names, f'{rel} should be in the backup'
    for p in dropped:
        rel = path.relpath(p, backup_home)
        assert rel not in names, f'{rel} should NOT be in the backup'


def test_backup_never_walks_ssh_directory(backup_home: str) -> None:
    """~/.ssh sits outside the two backed-up directories, so a backup
    never even reaches the device's keys."""
    ssh_dir = path.join(backup_home, '.ssh')
    os.makedirs(ssh_dir)
    with open(path.join(ssh_dir, 'authorized_keys'), 'wb') as f:
        f.write(_OWNER_KEY)
    with open(path.join(ssh_dir, 'id_ed25519'), 'wb') as f:
        f.write(b'-----BEGIN OPENSSH PRIVATE KEY-----\n')
    with open(path.join(backup_home, '.anthias', 'anthias.conf'), 'w') as f:
        f.write('[main]\n')

    archive = create_backup(name='ssh-outside-scope')
    with tarfile.open(path.join(backup_home, static_dir, archive)) as tar:
        names = tar.getnames()

    assert not [n for n in names if '.ssh' in n.split('/')]
    assert not [n for n in names if n.endswith('id_ed25519')]


# Version guard: a backup carries a manifest recording the schema it was
# made against. A restore refuses a backup from a newer release (whose
# data this system may not understand) and migrates an older one up.

_MIGRATE = 'anthias_server.lib.backup_helper._upgrade_restored_database'


def _add_manifest_member(tar: tarfile.TarFile, schema_version: int) -> None:
    manifest = {
        'format': 1,
        'anthias_version': '2026.9.0',
        'schema_version': schema_version,
        'created_at': '2026-09-20T00:00:00+00:00',
    }
    _add_file_member(
        tar, BACKUP_MANIFEST_NAME, json.dumps(manifest).encode('utf-8')
    )


def _write_backup_with_schema(archive: str, schema_version: int) -> None:
    # NOSONAR(python:S5042) — fixture builder, write mode.
    with tarfile.open(archive, 'w:gz') as tar:  # NOSONAR
        _add_manifest_member(tar, schema_version)
        _stage_legit_layout(tar)


def _require_schema() -> int:
    version = _current_schema_version()
    assert version is not None
    return version


def test_current_schema_version_matches_migration_files() -> None:
    """The schema version is the highest anthias_app migration number,
    read from disk without a database."""
    from anthias_server.app import migrations

    mig_dir = path.dirname(migrations.__file__)
    numbers = [
        int(f.split('_', 1)[0])
        for f in os.listdir(mig_dir)
        if f[:4].isdigit() and f.endswith('.py')
    ]
    assert _current_schema_version() == max(numbers)


def test_backup_writes_version_manifest(backup_home: str) -> None:
    """Every backup carries a manifest stamped with the current schema."""
    archive = create_backup(name='manifest-test')
    with tarfile.open(path.join(backup_home, static_dir, archive)) as tar:
        assert BACKUP_MANIFEST_NAME in tar.getnames()
        fobj = tar.extractfile(BACKUP_MANIFEST_NAME)
        assert fobj is not None
        manifest = json.loads(fobj.read().decode('utf-8'))

    assert manifest['format'] == 1
    assert manifest['schema_version'] == _require_schema()
    assert isinstance(manifest['anthias_version'], str)


def test_manifest_is_not_written_to_disk(backup_home: str) -> None:
    """The manifest is metadata, not runtime state — a restore reads it
    for the version check but never extracts it under $HOME."""
    archive = create_backup(name='manifest-ondisk')
    recover(path.join(backup_home, static_dir, archive))

    assert not path.exists(path.join(backup_home, BACKUP_MANIFEST_NAME))


def test_recover_rejects_newer_backup(backup_home: str) -> None:
    """A backup whose schema is newer than this system is refused before
    anything is written, and the migration is not triggered."""
    archive = path.join(backup_home, 'newer.tar.gz')
    _write_backup_with_schema(archive, _require_schema() + 1)

    with (
        mock.patch(_MIGRATE) as migrate,
        pytest.raises(IncompatibleBackupError),
    ):
        recover(archive)

    migrate.assert_not_called()
    # Guard fired before extraction: nothing landed on disk.
    assert not path.exists(path.join(backup_home, '.anthias', 'anthias.conf'))


def test_recover_accepts_older_backup_and_migrates(backup_home: str) -> None:
    """An older backup is restored and its database migrated up."""
    archive = path.join(backup_home, 'older.tar.gz')
    _write_backup_with_schema(archive, max(_require_schema() - 1, 0))

    with mock.patch(_MIGRATE) as migrate:
        recover(archive)

    assert path.isfile(path.join(backup_home, '.anthias', 'anthias.conf'))
    migrate.assert_called_once()


def test_recover_accepts_same_version_backup(backup_home: str) -> None:
    """A backup from the same schema restores cleanly."""
    archive = path.join(backup_home, 'same.tar.gz')
    _write_backup_with_schema(archive, _require_schema())

    with mock.patch(_MIGRATE):
        recover(archive)

    assert path.isfile(path.join(backup_home, '.anthias', 'anthias.conf'))


def test_recover_accepts_manifestless_legacy_backup(backup_home: str) -> None:
    """A pre-versioning backup (no manifest) predates every known schema,
    so it is accepted and migrated rather than rejected."""
    archive = path.join(backup_home, 'legacy.tar.gz')
    # NOSONAR(python:S5042) — fixture builder, write mode.
    with tarfile.open(archive, 'w:gz') as tar:  # NOSONAR
        _stage_legit_layout(tar)

    with mock.patch(_MIGRATE) as migrate:
        recover(archive)

    assert path.isfile(path.join(backup_home, '.anthias', 'anthias.conf'))
    migrate.assert_called_once()


def test_recover_lenient_when_local_schema_unknown(backup_home: str) -> None:
    """If this system can't read its own schema version, the guard must
    not block: it neither rejects a valid restore nor guesses. A
    would-be "newer" backup is accepted rather than wrongly refused."""
    archive = path.join(backup_home, 'unknown-local.tar.gz')
    _write_backup_with_schema(archive, 9999)

    with (
        mock.patch(
            'anthias_server.lib.backup_helper._current_schema_version',
            return_value=None,
        ),
        mock.patch(_MIGRATE),
    ):
        recover(archive)

    assert path.isfile(path.join(backup_home, '.anthias', 'anthias.conf'))


def test_backup_is_portable_across_deployments(tmp_path: Path) -> None:
    """A backup built on one deployment must restore on the other.

    Balena (data on a resin-data volume) and non-Balena (data on a host
    bind mount) both run with $HOME=/data and store the runtime under
    $HOME/.anthias and $HOME/anthias_assets, so every archive path is
    relative to $HOME — never an absolute or deployment-specific
    location. This simulates the two by using different absolute roots:
    build under one, restore under the other.
    """
    src_home = tmp_path / 'deployment_a'
    dst_home = tmp_path / 'deployment_b'
    for home in (src_home, dst_home):
        (home / '.anthias').mkdir(parents=True)
        (home / 'anthias_assets').mkdir(parents=True)
    (src_home / '.anthias' / 'anthias.conf').write_text('[main]\n')
    (src_home / '.anthias' / 'anthias.db').write_bytes(b'DB')
    (src_home / 'anthias_assets' / 'clip.mp4').write_bytes(b'VIDEO')

    with mock.patch.dict(os.environ, {'HOME': str(src_home)}):
        archive_name = create_backup(name='portable')
        archive_path = src_home / static_dir / archive_name
        with tarfile.open(archive_path) as tar:
            names = tar.getnames()
        # Nothing absolute or deployment-specific: only the manifest and
        # paths under the two runtime directories.
        assert all(not n.startswith('/') and not path.isabs(n) for n in names)
        assert all(
            n == BACKUP_MANIFEST_NAME
            or n.split('/')[0] in ('.anthias', 'anthias_assets')
            for n in names
        )
        moved = dst_home / archive_name
        shutil.copy(archive_path, moved)

    # Restore on the "other" deployment, rooted at a different absolute
    # path, and confirm the runtime lands correctly under it.
    with mock.patch.dict(os.environ, {'HOME': str(dst_home)}):
        recover(str(moved))

    assert (dst_home / '.anthias' / 'anthias.conf').is_file()
    assert (dst_home / '.anthias' / 'anthias.db').is_file()
    assert (dst_home / 'anthias_assets' / 'clip.mp4').is_file()


def test_manifest_carries_no_deployment_identifier(backup_home: str) -> None:
    """The version manifest describes the release, not the deployment, so
    a Balena backup and a non-Balena backup of the same version are
    interchangeable — no balena/resin/host-user keys leak in."""
    archive = create_backup(name='manifest-neutral')
    with tarfile.open(path.join(backup_home, static_dir, archive)) as tar:
        fobj = tar.extractfile(BACKUP_MANIFEST_NAME)
        assert fobj is not None
        manifest = json.loads(fobj.read().decode('utf-8'))

    assert set(manifest) == {
        'format',
        'anthias_version',
        'schema_version',
        'created_at',
    }
