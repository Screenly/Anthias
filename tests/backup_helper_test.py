import mock
import unittest
from datetime import datetime
from lib.backup_helper import create_backup

dt = datetime(2016, 7, 19, 12, 42, 12)

class BackupHelperTest(unittest.TestCase):
    def setUp(self):
        # @TODO: Delete all archive files in ~/screenly/static/*.tar.gz.
        shutil.rmtree('/home/pi/screenly/static/*.tar.gz', ignore_errors=True)
    def test_get_backup_name(self):
        with mock.patch('lib.backup_helper.datetime') as mock_datetime:
            mock_datetime.now.return_value = dt
            archive_name = create_backup()
            self.assertEqual(archive_name, 'screenly-backup-2016-07-19T12-42-12.tar.gz')
