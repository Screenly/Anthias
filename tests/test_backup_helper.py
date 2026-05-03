import os
import shutil
import tarfile
import tempfile
from collections.abc import Iterator
from datetime import datetime
from os import path
from unittest import mock

import pytest

from anthias_server.lib.backup_helper import (
    create_backup,
    recover,
    static_dir,
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
    dt = datetime(2016, 7, 19, 12, 42, 12)
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

    with mock.patch.dict(os.environ, {'HOME': legacy_home}):
        with pytest.raises(Exception):
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
