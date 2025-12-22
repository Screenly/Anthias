from __future__ import unicode_literals

import logging
import os

import mock
from unittest_parametrize import ParametrizedTestCase, parametrize

from lib.github import is_up_to_date

GIT_HASH_1 = 'da39a3ee5e6b4b0d3255bfef95601890afd80709'
GIT_SHORT_HASH_1 = 'da39a3e'
GIT_HASH_2 = '6adfb183a4a2c94a2f92dab5ade762a47889a5a1'
GIT_SHORT_HASH_2 = '6adfb18'


logging.disable(logging.CRITICAL)


class UpdateTest(ParametrizedTestCase):
    @mock.patch(
        'lib.github.fetch_remote_hash',
        mock.MagicMock(return_value=(None, False)),
    )
    def test__if_git_branch_env_does_not_exist__is_up_to_date_should_return_true(
        self,
    ):  # noqa: E501
        self.assertEqual(is_up_to_date(), True)

    @parametrize(
        'hashes, expected',
        [
            (
                {
                    'latest_remote_hash': GIT_HASH_1,
                    'git_hash': GIT_HASH_1,
                    'git_short_hash': GIT_SHORT_HASH_1,
                    'latest_docker_hub_hash': GIT_SHORT_HASH_1,
                },
                True,
            ),
            (
                {
                    'latest_remote_hash': GIT_HASH_2,
                    'git_hash': GIT_HASH_1,
                    'git_short_hash': GIT_SHORT_HASH_1,
                    'latest_docker_hub_hash': GIT_SHORT_HASH_1,
                },
                True,
            ),
            (
                {
                    'latest_remote_hash': GIT_HASH_1,
                    'git_hash': GIT_HASH_1,
                    'git_short_hash': GIT_SHORT_HASH_1,
                    'latest_docker_hub_hash': GIT_SHORT_HASH_2,
                },
                True,
            ),
            (
                {
                    'latest_remote_hash': GIT_HASH_2,
                    'git_hash': GIT_HASH_1,
                    'git_short_hash': GIT_SHORT_HASH_1,
                    'latest_docker_hub_hash': GIT_SHORT_HASH_2,
                },
                False,
            ),
        ],
    )
    @mock.patch(
        'lib.github.get_git_branch',
        mock.MagicMock(return_value='master'),
    )
    def test_is_up_to_date_should_return_value_depending_on_git_hashes(
        self, hashes, expected
    ):
        os.environ['GIT_BRANCH'] = 'master'
        os.environ['DEVICE_TYPE'] = 'pi4'

        latest_remote_hash = hashes['latest_remote_hash']
        git_hash = hashes['git_hash']
        git_short_hash = hashes['git_short_hash']
        latest_docker_hub_hash = hashes['latest_docker_hub_hash']

        with (
            mock.patch(
                'lib.github.fetch_remote_hash',
                mock.MagicMock(return_value=(latest_remote_hash, False)),
            ),
            mock.patch(
                'lib.github.get_git_hash',
                mock.MagicMock(return_value=git_hash),
            ),
            mock.patch(
                'lib.github.get_git_short_hash',
                mock.MagicMock(return_value=git_short_hash),
            ),
            mock.patch(
                'lib.github.get_latest_docker_hub_hash',
                mock.MagicMock(return_value=latest_docker_hub_hash),
            ),
        ):
            self.assertEqual(is_up_to_date(), expected)
