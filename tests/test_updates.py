import logging
from typing import Any
from unittest import mock

import pytest

from lib.github import is_up_to_date

GIT_HASH_1 = 'da39a3ee5e6b4b0d3255bfef95601890afd80709'
GIT_SHORT_HASH_1 = 'da39a3e'
GIT_HASH_2 = '6adfb183a4a2c94a2f92dab5ade762a47889a5a1'


logging.disable(logging.CRITICAL)


@mock.patch(
    'lib.github.fetch_remote_hash',
    mock.MagicMock(return_value=(None, False)),
)
def test_returns_true_when_git_branch_env_missing() -> None:
    assert is_up_to_date() is True


@pytest.mark.parametrize(
    'hashes,expected',
    [
        # Master HEAD matches local hash → up to date regardless of
        # whether the published image was found.
        (
            {
                'latest_remote_hash': GIT_HASH_1,
                'git_hash': GIT_HASH_1,
                'git_short_hash': GIT_SHORT_HASH_1,
                'is_running_latest_published_image': True,
            },
            True,
        ),
        # Master is ahead of local, but the running image manifest
        # matches `latest-<board>` on GHCR → up to date.
        (
            {
                'latest_remote_hash': GIT_HASH_2,
                'git_hash': GIT_HASH_1,
                'git_short_hash': GIT_SHORT_HASH_1,
                'is_running_latest_published_image': True,
            },
            True,
        ),
        # Master HEAD matches local even when GHCR check disagrees
        # (e.g. tag retention dropped our short-hash) → up to date.
        (
            {
                'latest_remote_hash': GIT_HASH_1,
                'git_hash': GIT_HASH_1,
                'git_short_hash': GIT_SHORT_HASH_1,
                'is_running_latest_published_image': False,
            },
            True,
        ),
        # Master is ahead AND the running image is older than
        # `latest-<board>` on GHCR → banner shown.
        (
            {
                'latest_remote_hash': GIT_HASH_2,
                'git_hash': GIT_HASH_1,
                'git_short_hash': GIT_SHORT_HASH_1,
                'is_running_latest_published_image': False,
            },
            False,
        ),
        # Master is ahead and GHCR lookup failed (None) → fail open
        # to "not up to date" so the banner shows rather than the
        # device sitting silently on a stale image.
        (
            {
                'latest_remote_hash': GIT_HASH_2,
                'git_hash': GIT_HASH_1,
                'git_short_hash': GIT_SHORT_HASH_1,
                'is_running_latest_published_image': None,
            },
            False,
        ),
    ],
)
def test_is_up_to_date_should_return_value_depending_on_git_hashes(
    hashes: dict[str, Any],
    expected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('GIT_BRANCH', 'master')
    monkeypatch.setenv('DEVICE_TYPE', 'pi4')

    latest_remote_hash = hashes['latest_remote_hash']
    git_hash = hashes['git_hash']
    git_short_hash = hashes['git_short_hash']
    published_match = hashes['is_running_latest_published_image']

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
            'lib.github.is_running_latest_published_image',
            mock.MagicMock(return_value=published_match),
        ),
    ):
        assert is_up_to_date() == expected
