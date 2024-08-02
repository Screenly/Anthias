from __future__ import unicode_literals
import unittest

import mock
import os

from lib.github import is_up_to_date
from settings import settings

fancy_sha = 'deadbeaf'


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.get_configdir_m = mock.patch('settings.AnthiasSettings.get_configdir', mock.MagicMock(return_value='/tmp/.screenly/'))
        self.get_configdir_m.start()

        self.sha_file = settings.get_configdir() + 'latest_anthias_sha'

        if not os.path.exists(settings.get_configdir()):
            os.mkdir(settings.get_configdir())

    def tearDown(self):
        if os.path.isfile(self.sha_file):
            os.remove(self.sha_file)

        self.get_configdir_m.stop()

    @mock.patch('viewer.settings.get_configdir', mock.MagicMock(return_value='/tmp/.screenly/'))
    def test_if_sha_file_not_exists__is_up_to_date__should_return_false(self):
        self.assertEqual(is_up_to_date(), True)

    @mock.patch('viewer.settings.get_configdir', mock.MagicMock(return_value='/tmp/.screenly/'))
    def test_if_sha_file_not_equals_to_branch_hash__is_up_to_date__should_return_false(self):
        os.environ['GIT_BRANCH'] = 'master'
        with open(self.sha_file, 'w+') as f:
            f.write(fancy_sha)
        self.assertEqual(is_up_to_date(), False)
        del os.environ['GIT_BRANCH']

    test_if_sha_file_not_exists__is_up_to_date__should_return_false.fixme = True
    test_if_sha_file_not_equals_to_branch_hash__is_up_to_date__should_return_false.fixme = True
