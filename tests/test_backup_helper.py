import shutil
import unittest
from datetime import datetime
from os import getenv, path

import mock

from lib.backup_helper import create_backup, recover, static_dir

home = getenv('HOME')


class BackupHelperTest(unittest.TestCase):
    def setUp(self):
        self.dt = datetime(2016, 7, 19, 12, 42, 12)
        self.expected_archive_name = (
            'anthias-backup-2016-07-19T12-42-12.tar.gz')
        self.assertFalse(path.isdir(path.join(home, static_dir)))

    def tearDown(self):
        shutil.rmtree(
            path.join(home, 'screenly'),
            ignore_errors=True,
        )

    def get_patched_datetime(self):
        return mock.patch('lib.backup_helper.datetime')

    def test_get_backup_name(self):
        with self.get_patched_datetime() as mock_datetime:
            mock_datetime.now.return_value = self.dt
            archive_name = create_backup()
            self.assertEqual(archive_name, self.expected_archive_name)

    def test_recover(self):
        # TODO: Make the tests more specific.
        #    For example, we can check if the individual files are present.
        archive_name = create_backup()
        file_path = path.join(home, static_dir, archive_name)
        self.assertTrue(path.isfile(file_path))
        recover(file_path)
        self.assertFalse(path.isfile(file_path))
