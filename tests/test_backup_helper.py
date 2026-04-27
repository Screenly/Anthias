import io
import os
import shutil
import tarfile
import tempfile
import unittest
from datetime import datetime
from os import path
from typing import Any
from unittest import mock

from lib.backup_helper import (
    BackupRecoverError,
    _safe_extract,
    create_backup,
    recover,
    static_dir,
)


class BackupHelperTest(unittest.TestCase):
    """Exercises create_backup() / recover() under a temporary $HOME so a
    developer running the test on a real workstation never has their
    ~/anthias checkout or ~/.anthias config wiped by tearDown's
    rmtree."""

    def setUp(self) -> None:
        self.tmp_home = tempfile.mkdtemp(prefix='anthias-backup-test-')
        # Populate the layout create_backup() expects to tar up so the
        # call has something to read.
        os.makedirs(path.join(self.tmp_home, '.anthias'))
        os.makedirs(path.join(self.tmp_home, 'anthias_assets'))

        self._home_patch = mock.patch.dict(
            os.environ, {'HOME': self.tmp_home}
        )
        self._home_patch.start()

        self.dt = datetime(2016, 7, 19, 12, 42, 12)
        self.expected_archive_name = (
            'anthias-backup-2016-07-19T12-42-12.tar.gz'
        )
        self.assertFalse(path.isdir(path.join(self.tmp_home, static_dir)))

    def tearDown(self) -> None:
        self._home_patch.stop()
        shutil.rmtree(self.tmp_home, ignore_errors=True)

    def get_patched_datetime(self) -> Any:
        return mock.patch('lib.backup_helper.datetime')

    def test_get_backup_name(self) -> None:
        with self.get_patched_datetime() as mock_datetime:
            mock_datetime.now.return_value = self.dt
            archive_name = create_backup()
            self.assertEqual(archive_name, self.expected_archive_name)

    def test_recover(self) -> None:
        archive_name = create_backup()
        file_path = path.join(self.tmp_home, static_dir, archive_name)
        self.assertTrue(path.isfile(file_path))
        recover(file_path)
        self.assertFalse(path.isfile(file_path))


def _build_archive_with(member: tarfile.TarInfo, data: bytes = b'') -> str:
    """Build a single-member tar.gz in a temp file and return its path."""
    fd, archive_path = tempfile.mkstemp(suffix='.tar.gz')
    os.close(fd)
    # Test fixture: building a tar archive for the safe-extract tests.
    with tarfile.open(archive_path, 'w:gz') as tar:  # NOSONAR
        if data:
            tar.addfile(member, io.BytesIO(data))
        else:
            tar.addfile(member)
    return archive_path


class SafeExtractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dest = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.dest, ignore_errors=True)

    def _open_and_extract(self, archive_path: str) -> None:
        try:
            # Test exercises the safe-extract helper directly.
            with tarfile.open(archive_path, 'r:gz') as tar:  # NOSONAR
                _safe_extract(tar, self.dest)
        finally:
            os.remove(archive_path)

    def test_rejects_relative_traversal(self) -> None:
        member = tarfile.TarInfo(name='../escaped.txt')
        member.size = 3
        archive_path = _build_archive_with(member, b'bad')
        with self.assertRaises(BackupRecoverError):
            self._open_and_extract(archive_path)

    def test_rejects_absolute_path(self) -> None:
        member = tarfile.TarInfo(name='/etc/passwd-pwn')
        member.size = 3
        archive_path = _build_archive_with(member, b'bad')
        with self.assertRaises(BackupRecoverError):
            self._open_and_extract(archive_path)

    def test_rejects_symlink_member(self) -> None:
        member = tarfile.TarInfo(name='link')
        member.type = tarfile.SYMTYPE
        member.linkname = '/etc/passwd'
        archive_path = _build_archive_with(member)
        with self.assertRaises(BackupRecoverError):
            self._open_and_extract(archive_path)

    def test_rejects_fifo_member(self) -> None:
        member = tarfile.TarInfo(name='pipe')
        member.type = tarfile.FIFOTYPE
        archive_path = _build_archive_with(member)
        with self.assertRaises(BackupRecoverError):
            self._open_and_extract(archive_path)

    def test_extracts_regular_file(self) -> None:
        member = tarfile.TarInfo(name='inside.txt')
        member.size = 5
        archive_path = _build_archive_with(member, b'hello')
        self._open_and_extract(archive_path)
        self.assertTrue(path.isfile(path.join(self.dest, 'inside.txt')))


class RecoverLegacyTarballTest(unittest.TestCase):
    """Backups produced by pre-rename releases used `.screenly` and
    `screenly_assets` as top-level archive entries. recover() must keep
    accepting them so users can still restore those backups."""

    def setUp(self) -> None:
        self.tmp_home = tempfile.mkdtemp(prefix='anthias-backup-legacy-test-')

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_home, ignore_errors=True)

    def _build_legacy_tarball(self) -> str:
        # Stage the legacy layout in a scratch dir, then tar it up with
        # top-level `.screenly/` and `screenly_assets/` arcnames.
        scratch = tempfile.mkdtemp(prefix='anthias-backup-stage-')
        try:
            os.makedirs(path.join(scratch, '.screenly'))
            os.makedirs(path.join(scratch, 'screenly_assets'))
            with open(
                path.join(scratch, '.screenly', 'screenly.conf'), 'w'
            ) as f:
                f.write('[main]\nconfigdir = .screenly\n')
            with open(
                path.join(scratch, 'screenly_assets', 'a.mp4'), 'wb'
            ) as f:
                f.write(b'video-stub')

            archive = path.join(self.tmp_home, 'legacy-backup.tar.gz')
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

    def test_recover_accepts_legacy_archive(self) -> None:
        archive = self._build_legacy_tarball()

        with mock.patch.dict(os.environ, {'HOME': self.tmp_home}):
            recover(archive)

        # Archive removed (recover() unlinks on success).
        self.assertFalse(path.isfile(archive))
        # Legacy entries restored under the patched HOME.
        self.assertTrue(
            path.isfile(path.join(self.tmp_home, '.screenly', 'screenly.conf'))
        )
        self.assertTrue(
            path.isfile(path.join(self.tmp_home, 'screenly_assets', 'a.mp4'))
        )

    def test_recover_rejects_unrelated_archive(self) -> None:
        archive = path.join(self.tmp_home, 'random.tar.gz')
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

        with mock.patch.dict(os.environ, {'HOME': self.tmp_home}):
            with self.assertRaises(Exception):
                recover(archive)

    def test_recover_skips_path_traversal_member(self) -> None:
        """A malicious tarball with a `..` member must not write outside
        $HOME. The required top-level entries are still present, so
        recover() proceeds, but the unsafe member should be skipped."""
        archive = path.join(self.tmp_home, 'malicious.tar.gz')
        scratch = tempfile.mkdtemp(prefix='anthias-backup-mal-')
        try:
            os.makedirs(path.join(scratch, '.anthias'))
            os.makedirs(path.join(scratch, 'anthias_assets'))
            with open(
                path.join(scratch, '.anthias', 'anthias.conf'), 'w'
            ) as f:
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

        with mock.patch.dict(os.environ, {'HOME': self.tmp_home}):
            recover(archive)

        # Legit member extracted; hostile one skipped.
        self.assertTrue(
            path.isfile(path.join(self.tmp_home, '.anthias', 'anthias.conf'))
        )
        parent_of_home = path.dirname(self.tmp_home)
        self.assertFalse(path.exists(path.join(parent_of_home, 'evil.txt')))
