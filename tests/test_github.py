import logging
from collections.abc import Iterator
from typing import Any
from unittest import mock
from unittest.mock import MagicMock

import pytest
from requests import exceptions as requests_exceptions

from lib import github

logging.disable(logging.CRITICAL)


@pytest.fixture
def redis_data() -> dict[str, str]:
    return {}


@pytest.fixture
def fake_redis(redis_data: dict[str, str]) -> MagicMock:
    fake = MagicMock()
    fake.get.side_effect = redis_data.get
    fake.set.side_effect = lambda key, value: redis_data.__setitem__(
        key, value
    )
    fake.expire.side_effect = lambda _key, _ttl: None
    return fake


@pytest.fixture
def github_env(fake_redis: MagicMock) -> Iterator[None]:
    with mock.patch.object(github, 'r', fake_redis):
        yield


def _resp(status_code: int = 200, json_data: Any = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    if json_data is not None:
        resp.json.return_value = json_data
    if status_code >= 400:

        def _raise() -> None:
            raise requests_exceptions.HTTPError(
                f'{status_code} error', response=resp
            )

        resp.raise_for_status.side_effect = _raise
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# remote_branch_available
# ---------------------------------------------------------------------------


def test_remote_branch_available_no_branch(github_env: None) -> None:
    assert github.remote_branch_available(None) is None
    assert github.remote_branch_available('') is None


def test_remote_branch_available_happy_path(
    github_env: None, redis_data: dict[str, str]
) -> None:
    with mock.patch.object(
        github, 'requests_get', return_value=_resp(200)
    ) as mock_get:
        result = github.remote_branch_available('master')
    assert result is True
    mock_get.assert_called_once()
    url = mock_get.call_args.args[0]
    # New behavior (#2797): direct branch endpoint, not /branches list.
    assert 'branches/master' in url
    assert redis_data['remote-branch-available'] == '1'


def test_remote_branch_available_404_returns_false(
    github_env: None, redis_data: dict[str, str]
) -> None:
    with mock.patch.object(github, 'requests_get', return_value=_resp(404)):
        assert github.remote_branch_available('nope') is False
    assert redis_data['remote-branch-available'] == '0'


def test_remote_branch_available_request_exception(
    github_env: None, redis_data: dict[str, str]
) -> None:
    with mock.patch.object(
        github,
        'requests_get',
        side_effect=requests_exceptions.ConnectionError(),
    ):
        assert github.remote_branch_available('master') is None
    # Backoff key was set.
    assert 'github-api-error' in redis_data


def test_remote_branch_available_5xx_triggers_backoff(
    github_env: None, redis_data: dict[str, str]
) -> None:
    with mock.patch.object(github, 'requests_get', return_value=_resp(500)):
        assert github.remote_branch_available('master') is None
    assert 'github-api-error' in redis_data


def test_remote_branch_available_uses_cached_hit(
    github_env: None, redis_data: dict[str, str]
) -> None:
    redis_data['remote-branch-available'] = '1'
    with mock.patch.object(github, 'requests_get') as mock_get:
        assert github.remote_branch_available('master') is True
    mock_get.assert_not_called()

    redis_data['remote-branch-available'] = '0'
    with mock.patch.object(github, 'requests_get') as mock_get:
        assert github.remote_branch_available('master') is False
    mock_get.assert_not_called()


def test_remote_branch_available_skips_when_backoff_active(
    github_env: None, redis_data: dict[str, str]
) -> None:
    redis_data['github-api-error'] = 'something'
    with mock.patch.object(github, 'requests_get') as mock_get:
        assert github.remote_branch_available('master') is None
    mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# fetch_remote_hash
# ---------------------------------------------------------------------------


def test_fetch_remote_hash_no_branch_env(
    github_env: None, monkeypatch: Any
) -> None:
    monkeypatch.delenv('GIT_BRANCH', raising=False)
    assert github.fetch_remote_hash() == (None, False)


def test_fetch_remote_hash_cache_hit(
    github_env: None, redis_data: dict[str, str], monkeypatch: Any
) -> None:
    monkeypatch.setenv('GIT_BRANCH', 'master')
    redis_data['latest-remote-hash'] = 'abc123'
    with mock.patch.object(github, 'requests_get') as mock_get:
        result = github.fetch_remote_hash()
    assert result == ('abc123', False)
    mock_get.assert_not_called()


def test_fetch_remote_hash_short_circuits_when_branch_unavailable(
    github_env: None, monkeypatch: Any
) -> None:
    monkeypatch.setenv('GIT_BRANCH', 'master')
    with mock.patch.object(
        github, 'remote_branch_available', return_value=False
    ):
        result = github.fetch_remote_hash()
    assert result == (None, False)


def test_fetch_remote_hash_happy_path(
    github_env: None,
    redis_data: dict[str, str],
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv('GIT_BRANCH', 'master')
    resp = _resp(200, json_data={'object': {'sha': 'abc123'}})
    with (
        mock.patch.object(
            github, 'remote_branch_available', return_value=True
        ),
        mock.patch.object(github, 'requests_get', return_value=resp),
    ):
        result = github.fetch_remote_hash()
    assert result == ('abc123', True)
    assert redis_data['latest-remote-hash'] == 'abc123'


def test_fetch_remote_hash_request_exception(
    github_env: None, redis_data: dict[str, str], monkeypatch: Any
) -> None:
    monkeypatch.setenv('GIT_BRANCH', 'master')
    with (
        mock.patch.object(
            github, 'remote_branch_available', return_value=True
        ),
        mock.patch.object(
            github,
            'requests_get',
            side_effect=requests_exceptions.ConnectionError(),
        ),
    ):
        result = github.fetch_remote_hash()
    assert result == (None, False)
    assert 'github-api-error' in redis_data


# ---------------------------------------------------------------------------
# _get_ghcr_anonymous_token
# ---------------------------------------------------------------------------


def test_get_ghcr_anonymous_token_happy_path(github_env: None) -> None:
    resp = _resp(200, json_data={'token': 'tok-123'})
    with mock.patch.object(github, 'requests_get', return_value=resp):
        assert github._get_ghcr_anonymous_token() == 'tok-123'


def test_get_ghcr_anonymous_token_request_exception(
    github_env: None, redis_data: dict[str, str]
) -> None:
    with mock.patch.object(
        github,
        'requests_get',
        side_effect=requests_exceptions.ConnectionError(),
    ):
        assert github._get_ghcr_anonymous_token() is None
    assert redis_data['ghcr-api-error'] == '1'


def test_get_ghcr_anonymous_token_value_error_on_json(
    github_env: None, redis_data: dict[str, str]
) -> None:
    resp = _resp(200)
    resp.json.side_effect = ValueError('bad json')
    with mock.patch.object(github, 'requests_get', return_value=resp):
        assert github._get_ghcr_anonymous_token() is None
    assert redis_data['ghcr-api-error'] == '1'


def test_get_ghcr_anonymous_token_missing_field(
    github_env: None, redis_data: dict[str, str]
) -> None:
    resp = _resp(200, json_data={'not_token': 'abc'})
    with mock.patch.object(github, 'requests_get', return_value=resp):
        assert github._get_ghcr_anonymous_token() is None
    assert redis_data['ghcr-api-error'] == '1'


def test_get_ghcr_anonymous_token_non_string_field(
    github_env: None, redis_data: dict[str, str]
) -> None:
    resp = _resp(200, json_data={'token': 12345})
    with mock.patch.object(github, 'requests_get', return_value=resp):
        assert github._get_ghcr_anonymous_token() is None
    assert redis_data['ghcr-api-error'] == '1'


# ---------------------------------------------------------------------------
# _get_ghcr_manifest_digest
# ---------------------------------------------------------------------------


def test_get_ghcr_manifest_digest_happy_path(github_env: None) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {'Docker-Content-Digest': 'sha256:abc'}
    with mock.patch.object(github, 'requests_head', return_value=resp):
        assert github._get_ghcr_manifest_digest('tag', 'tok') == 'sha256:abc'


def test_get_ghcr_manifest_digest_404_no_backoff(
    github_env: None, redis_data: dict[str, str]
) -> None:
    resp = MagicMock()
    resp.status_code = 404
    resp.headers = {}
    with mock.patch.object(github, 'requests_head', return_value=resp):
        assert github._get_ghcr_manifest_digest('tag', 'tok') is None
    assert 'ghcr-api-error' not in redis_data


def test_get_ghcr_manifest_digest_5xx_with_backoff(
    github_env: None, redis_data: dict[str, str]
) -> None:
    resp = MagicMock()
    resp.status_code = 500
    resp.headers = {}
    with mock.patch.object(github, 'requests_head', return_value=resp):
        assert github._get_ghcr_manifest_digest('tag', 'tok') is None
    assert redis_data['ghcr-api-error'] == '1'


def test_get_ghcr_manifest_digest_429_with_backoff(
    github_env: None, redis_data: dict[str, str]
) -> None:
    resp = MagicMock()
    resp.status_code = 429
    resp.headers = {}
    with mock.patch.object(github, 'requests_head', return_value=resp):
        assert github._get_ghcr_manifest_digest('tag', 'tok') is None
    assert redis_data['ghcr-api-error'] == '1'


def test_get_ghcr_manifest_digest_request_exception(
    github_env: None, redis_data: dict[str, str]
) -> None:
    with mock.patch.object(
        github,
        'requests_head',
        side_effect=requests_exceptions.ConnectionError(),
    ):
        assert github._get_ghcr_manifest_digest('tag', 'tok') is None
    assert redis_data['ghcr-api-error'] == '1'


def test_get_ghcr_manifest_digest_missing_header_with_backoff(
    github_env: None, redis_data: dict[str, str]
) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    with mock.patch.object(github, 'requests_head', return_value=resp):
        assert github._get_ghcr_manifest_digest('tag', 'tok') is None
    assert redis_data['ghcr-api-error'] == '1'


def test_get_ghcr_manifest_digest_empty_header_with_backoff(
    github_env: None, redis_data: dict[str, str]
) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {'Docker-Content-Digest': ''}
    with mock.patch.object(github, 'requests_head', return_value=resp):
        assert github._get_ghcr_manifest_digest('tag', 'tok') is None
    assert redis_data['ghcr-api-error'] == '1'


# ---------------------------------------------------------------------------
# is_running_latest_published_image
# ---------------------------------------------------------------------------


def test_is_running_latest_published_image_missing_inputs(
    github_env: None,
) -> None:
    assert github.is_running_latest_published_image(None, 'pi4') is None
    assert github.is_running_latest_published_image('abc1234', None) is None
    assert github.is_running_latest_published_image('', 'pi4') is None
    assert github.is_running_latest_published_image('abc1234', '') is None


def test_is_running_latest_published_image_cache_hit_match(
    github_env: None, redis_data: dict[str, str]
) -> None:
    redis_data['latest-published-image-match:pi4:abc1234'] = '1'
    assert github.is_running_latest_published_image('abc1234', 'pi4') is True


def test_is_running_latest_published_image_cache_hit_mismatch(
    github_env: None, redis_data: dict[str, str]
) -> None:
    redis_data['latest-published-image-match:pi4:abc1234'] = '0'
    assert github.is_running_latest_published_image('abc1234', 'pi4') is False


def test_is_running_latest_published_image_cache_hit_unknown(
    github_env: None, redis_data: dict[str, str]
) -> None:
    redis_data['latest-published-image-match:pi4:abc1234'] = '?'
    assert github.is_running_latest_published_image('abc1234', 'pi4') is None


def test_is_running_latest_published_image_backoff_active(
    github_env: None, redis_data: dict[str, str]
) -> None:
    redis_data['ghcr-api-error'] = '1'
    assert github.is_running_latest_published_image('abc1234', 'pi4') is None


def test_is_running_latest_published_image_no_token(github_env: None) -> None:
    with mock.patch.object(
        github, '_get_ghcr_anonymous_token', return_value=None
    ):
        assert (
            github.is_running_latest_published_image('abc1234', 'pi4') is None
        )


def test_is_running_latest_published_image_no_latest_digest(
    github_env: None,
) -> None:
    with (
        mock.patch.object(
            github, '_get_ghcr_anonymous_token', return_value='tok'
        ),
        mock.patch.object(
            github, '_get_ghcr_manifest_digest', return_value=None
        ),
    ):
        assert (
            github.is_running_latest_published_image('abc1234', 'pi4') is None
        )


def test_is_running_latest_published_image_current_404_caches_unknown(
    github_env: None, redis_data: dict[str, str]
) -> None:
    # First call returns latest digest, second returns None (404).
    digests = ['sha256:latest', None]
    with (
        mock.patch.object(
            github, '_get_ghcr_anonymous_token', return_value='tok'
        ),
        mock.patch.object(
            github,
            '_get_ghcr_manifest_digest',
            side_effect=digests,
        ),
    ):
        assert (
            github.is_running_latest_published_image('abc1234', 'pi4') is None
        )
    assert redis_data['latest-published-image-match:pi4:abc1234'] == '?'


def test_is_running_latest_published_image_match(
    github_env: None, redis_data: dict[str, str]
) -> None:
    digests = ['sha256:same', 'sha256:same']
    with (
        mock.patch.object(
            github, '_get_ghcr_anonymous_token', return_value='tok'
        ),
        mock.patch.object(
            github,
            '_get_ghcr_manifest_digest',
            side_effect=digests,
        ),
    ):
        assert (
            github.is_running_latest_published_image('abc1234', 'pi4') is True
        )
    assert redis_data['latest-published-image-match:pi4:abc1234'] == '1'


def test_is_running_latest_published_image_mismatch(
    github_env: None, redis_data: dict[str, str]
) -> None:
    digests = ['sha256:latest', 'sha256:older']
    with (
        mock.patch.object(
            github, '_get_ghcr_anonymous_token', return_value='tok'
        ),
        mock.patch.object(
            github,
            '_get_ghcr_manifest_digest',
            side_effect=digests,
        ),
    ):
        assert (
            github.is_running_latest_published_image('abc1234', 'pi4') is False
        )
    assert redis_data['latest-published-image-match:pi4:abc1234'] == '0'


def test_is_running_latest_published_image_cache_key_scoped(
    github_env: None, redis_data: dict[str, str]
) -> None:
    """Cache verdict for one (board, hash) doesn't leak to another."""
    redis_data['latest-published-image-match:pi4:abc1234'] = '1'
    # Different hash → not a hit → fall through to lookup logic.
    with mock.patch.object(
        github, '_get_ghcr_anonymous_token', return_value=None
    ):
        assert (
            github.is_running_latest_published_image('def5678', 'pi4') is None
        )


# ---------------------------------------------------------------------------
# handle_github_error
# ---------------------------------------------------------------------------


def test_handle_github_error_with_response(
    github_env: None, redis_data: dict[str, str]
) -> None:
    inner_resp = MagicMock()
    inner_resp.content = b'rate limited'
    exc = requests_exceptions.HTTPError(response=inner_resp)
    github.handle_github_error(exc, 'test-action')
    assert redis_data['github-api-error'] == 'test-action'


def test_handle_github_error_without_response(
    github_env: None, redis_data: dict[str, str]
) -> None:
    exc = requests_exceptions.ConnectionError()
    exc.response = None
    github.handle_github_error(exc, 'no-resp-action')
    assert redis_data['github-api-error'] == 'no-resp-action'
