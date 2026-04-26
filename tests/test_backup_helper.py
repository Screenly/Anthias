import io
import shutil
import tarfile
import tempfile
import unittest
from datetime import datetime
from os import getenv, path
from typing import Any

import mock

from lib.backup_helper import (
    BackupRecoverError,
    _safe_extract,
    create_backup,
    recover,
    static_dir,
)

home = getenv('HOME') or ''


class BackupHelperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dt = datetime(2016, 7, 19, 12, 42, 12)
        self.expected_archive_name = (
            'anthias-backup-2016-07-19T12-42-12.tar.gz'
        )
        self.assertFalse(path.isdir(path.join(home, static_dir)))

    def tearDown(self) -> None:
        shutil.rmtree(
            path.join(home, 'screenly'),
            ignore_errors=True,
        )

    def get_patched_datetime(self) -> Any:
        return mock.patch('lib.backup_helper.datetime')

    def test_get_backup_name(self) -> None:
        with self.get_patched_datetime() as mock_datetime:
            mock_datetime.now.return_value = self.dt
            archive_name = create_backup()
            self.assertEqual(archive_name, self.expected_archive_name)

    def test_recover(self) -> None:
        # TODO: Make the tests more specific.
        #    For example, we can check if the individual files are present.
        archive_name = create_backup()
        file_path = path.join(home, static_dir, archive_name)
        self.assertTrue(path.isfile(file_path))
        recover(file_path)
        self.assertFalse(path.isfile(file_path))


def _build_archive_with(member: tarfile.TarInfo, data: bytes = b'') -> str:
    """Build a single-member tar.gz in a temp file and return its path."""
    fd, archive_path = tempfile.mkstemp(suffix='.tar.gz')
    import os

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
            import os

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
