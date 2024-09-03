from __future__ import unicode_literals
from unittest import TestCase

import mock
import os

from lib.github import is_up_to_date
from settings import settings

GIT_HASH_1 = 'da39a3ee5e6b4b0d3255bfef95601890afd80709'
GIT_SHORT_HASH_1 = 'da39a3e'
GIT_HASH_2 = '6adfb183a4a2c94a2f92dab5ade762a47889a5a1'
GIT_SHORT_HASH_2 = '6adfb18'


class UpdateTest(TestCase):
    def setUp(self):
        self.get_configdir_m = mock.patch(
            'settings.AnthiasSettings.get_configdir',
            mock.MagicMock(return_value='/tmp/.screenly/'),
        )
        self.get_configdir_m.start()

        self.sha_file = settings.get_configdir() + 'latest_anthias_sha'

        if not os.path.exists(settings.get_configdir()):
            os.mkdir(settings.get_configdir())

    def tearDown(self):
        if os.path.isfile(self.sha_file):
            os.remove(self.sha_file)

        self.get_configdir_m.stop()

    @mock.patch(
        'lib.github.fetch_remote_hash',
        mock.MagicMock(return_value=(None, False)),
    )
    def test__if_git_branch_env_does_not_exist__is_up_to_date_should_return_true(self):  # noqa: E501
        self.assertEqual(is_up_to_date(), True)

    @mock.patch(
        'lib.github.get_git_branch',
        mock.MagicMock(return_value='master'),
    )
    @mock.patch(
        'lib.github.get_latest_docker_hub_hash',
        mock.MagicMock(return_value=GIT_SHORT_HASH_1),
    )
    @mock.patch(
        'lib.github.get_git_short_hash',
        mock.MagicMock(return_value=GIT_SHORT_HASH_1),
    )
    @mock.patch(
        'lib.github.get_git_hash',
        mock.MagicMock(return_value=GIT_HASH_1),
    )
    @mock.patch(
        'lib.github.fetch_remote_hash',
        mock.MagicMock(return_value=(GIT_HASH_1, False)),
    )
    def test__if_git_hash_is_equal_to_latest_remote_hash__is_up_to_date_should_return_true(self):
        os.environ['GIT_BRANCH'] = 'master'
        os.environ['DEVICE_TYPE'] = 'pi4'

        self.assertEqual(is_up_to_date(), True)
