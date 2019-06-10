from datetime import datetime
from datetime import timedelta
import unittest

import mock
import viewer
import server
import os

from settings import settings

fancy_sha = 'deadbeaf'


def mocked_req_get(*args, **kwargs):
    class MockResponse:
        def __init__(self, content, status_code):
            self.content = content
            self.status_code = status_code

    return MockResponse('response_content\n', 200)


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.get_configdir_m = mock.patch('settings.ScreenlySettings.get_configdir', mock.MagicMock(return_value='/tmp/.screenly/'))
        self.get_configdir_m.start()

        self.sha_file = settings.get_configdir() + 'latest_screenly_sha'

        if not os.path.exists(settings.get_configdir()):
            os.mkdir(settings.get_configdir())

    def tearDown(self):
        if os.path.isfile(self.sha_file):
            os.remove(self.sha_file)

        self.get_configdir_m.stop()

    @mock.patch('viewer.settings.get_configdir', mock.MagicMock(return_value='/tmp/.screenly/'))
    def test_if_sha_file_not_exists__is_up_to_date__should_return_false(self):
        self.assertEqual(server.is_up_to_date(), True)

    @mock.patch('viewer.settings.get_configdir', mock.MagicMock(return_value='/tmp/.screenly/'))
    def test_if_sha_file_not_equals_to_branch_hash__is_up_to_date__should_return_false(self):
        with open(self.sha_file, 'w+') as f:
            f.write(fancy_sha)
        self.assertEqual(server.is_up_to_date(), False)

    @mock.patch('viewer.settings.get_configdir', mock.MagicMock(return_value='/tmp/.screenly/'))
    def test_if_sha_file_is_new__check_update__should_return_false(self):
        with open(self.sha_file, 'w+') as f:
            f.write(fancy_sha)
        self.assertEqual(viewer.check_update(), False)

        # check that SHA file not modified
        with open(self.sha_file, 'r') as f:
            self.assertEqual(f.readline(), fancy_sha)

    @mock.patch('viewer.req_get', side_effect=mocked_req_get)
    @mock.patch('viewer.remote_branch_available', side_effect=lambda _: True)
    @mock.patch('viewer.fetch_remote_hash', side_effect=lambda _: 'master')
    @mock.patch('viewer.settings.get_configdir', mock.MagicMock(return_value='/tmp/.screenly/'))
    def test_if_sha_file_is_empty__check_update__should_return_true(self, req_get, remote_branch_available, fetch_remote_hash):
        with open(self.sha_file, 'w+') as f:
            pass

        epoch = datetime.utcfromtimestamp(0)
        yesterday = datetime.now() - timedelta(days=2)
        dt = (yesterday - epoch).total_seconds()

        os.utime(self.sha_file, (dt, dt))

        self.assertEqual(viewer.check_update(), True)

        # check that file contains latest SHA
        with open(self.sha_file, 'r') as f:
            self.assertNotEqual(f.readline(), '')
