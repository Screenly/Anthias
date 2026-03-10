import shutil
from datetime import datetime
from os import getenv, path
from unittest.mock import patch

import pytest

from lib.backup_helper import create_backup, recover, static_dir

home = getenv('HOME')


class TestBackupHelper:
    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        self.dt = datetime(2016, 7, 19, 12, 42, 12)
        self.expected_archive_name = (
            'anthias-backup-2016-07-19T12-42-12.tar.gz'
        )
        assert not path.isdir(path.join(home, static_dir))
        yield
        shutil.rmtree(
            path.join(home, 'screenly'),
            ignore_errors=True,
        )

    def test_get_backup_name(self):
        with patch('lib.backup_helper.datetime') as mock_datetime:
            mock_datetime.now.return_value = self.dt
            archive_name = create_backup()
            assert archive_name == self.expected_archive_name

    def test_recover(self):
        archive_name = create_backup()
        file_path = path.join(home, static_dir, archive_name)
        assert path.isfile(file_path)
        recover(file_path)
        assert not path.isfile(file_path)
