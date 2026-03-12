import logging
import os
from unittest.mock import MagicMock, patch

import pytest

from lib.github import is_up_to_date

GIT_HASH_1 = 'da39a3ee5e6b4b0d3255bfef95601890afd80709'
GIT_SHORT_HASH_1 = 'da39a3e'
GIT_HASH_2 = '6adfb183a4a2c94a2f92dab5ade762a47889a5a1'
GIT_SHORT_HASH_2 = '6adfb18'

logging.disable(logging.CRITICAL)


class TestUpdates:
    @patch(
        'lib.github.fetch_remote_hash',
        MagicMock(return_value=(None, False)),
    )
    def test_no_git_branch_env_returns_true(self):
        assert is_up_to_date() is True

    @pytest.mark.parametrize(
        'hashes, expected',
        [
            (
                {
                    'latest_remote_hash': GIT_HASH_1,
                    'git_hash': GIT_HASH_1,
                    'git_short_hash': GIT_SHORT_HASH_1,
                    'latest_docker_hub_hash': (GIT_SHORT_HASH_1),
                },
                True,
            ),
            (
                {
                    'latest_remote_hash': GIT_HASH_2,
                    'git_hash': GIT_HASH_1,
                    'git_short_hash': GIT_SHORT_HASH_1,
                    'latest_docker_hub_hash': (GIT_SHORT_HASH_1),
                },
                True,
            ),
            (
                {
                    'latest_remote_hash': GIT_HASH_1,
                    'git_hash': GIT_HASH_1,
                    'git_short_hash': GIT_SHORT_HASH_1,
                    'latest_docker_hub_hash': (GIT_SHORT_HASH_2),
                },
                True,
            ),
            (
                {
                    'latest_remote_hash': GIT_HASH_2,
                    'git_hash': GIT_HASH_1,
                    'git_short_hash': GIT_SHORT_HASH_1,
                    'latest_docker_hub_hash': (GIT_SHORT_HASH_2),
                },
                False,
            ),
        ],
    )
    @patch(
        'lib.github.get_git_branch',
        MagicMock(return_value='master'),
    )
    def test_is_up_to_date_depends_on_git_hashes(self, hashes, expected):
        os.environ['GIT_BRANCH'] = 'master'
        os.environ['DEVICE_TYPE'] = 'pi4'

        with (
            patch(
                'lib.github.fetch_remote_hash',
                MagicMock(
                    return_value=(
                        hashes['latest_remote_hash'],
                        False,
                    )
                ),
            ),
            patch(
                'lib.github.get_git_hash',
                MagicMock(return_value=hashes['git_hash']),
            ),
            patch(
                'lib.github.get_git_short_hash',
                MagicMock(return_value=hashes['git_short_hash']),
            ),
            patch(
                'lib.github.get_latest_docker_hub_hash',
                MagicMock(return_value=hashes['latest_docker_hub_hash']),
            ),
        ):
            assert is_up_to_date() is expected
