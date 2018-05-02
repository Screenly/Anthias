from datetime import datetime
from datetime import timedelta
import unittest
from mock import patch
from settings import settings
import viewer
import server
import os
import shutil

fancy_sha = 'deadbeaf'


def mocked_req_get(*args, **kwargs):
    class MockResponse:
        def __init__(self, content, status_code):
            self.content = content
            self.status_code = status_code

    return MockResponse('response_content\n', 200)


class UpdateTest(unittest.TestCase):
    def setUp(self):
        settings.home = '/tmp/'
        self.sha_file = settings.get_configdir() + 'latest_screenly_sha'

        if not os.path.exists(settings.get_configdir()):
            os.mkdir(settings.get_configdir())

    def tearDown(self):
        shutil.rmtree(settings.get_configdir())

    def test_if_sha_file_not_exists__is_up_to_date__should_return_false(self):
        self.assertEqual(server.is_up_to_date(), True)

    def test_if_sha_file_not_equals_to_branch_hash__is_up_to_date__should_return_false(self):
        with open(self.sha_file, 'w+') as f:
            f.write(fancy_sha)
        self.assertEqual(server.is_up_to_date(), False)

    def test_if_sha_file_is_new__check_update__should_return_false(self):
        with open(self.sha_file, 'w+') as f:
            f.write(fancy_sha)
        self.assertEqual(viewer.check_update(), False)

        # check that SHA file not modified
        with open(self.sha_file, 'r') as f:
            self.assertEqual(f.readline(), fancy_sha)

    @patch('viewer.req_get', side_effect=mocked_req_get)
    @patch('viewer.remote_branch_available', side_effect=lambda _: True)
    @patch('viewer.fetch_remote_hash', side_effect=lambda _: 'master')
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
