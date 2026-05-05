import logging
from collections.abc import Iterator
from typing import Any
from unittest import mock
from unittest.mock import MagicMock

import pytest
from requests import exceptions as requests_exceptions

from anthias_server.lib import github

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
# _fetch_latest_release_tag
# ---------------------------------------------------------------------------


def test_fetch_latest_release_tag_cache_hit(
    github_env: None, redis_data: dict[str, str]
) -> None:
    redis_data[github.LATEST_RELEASE_TAG_KEY] = 'v2026.5.0'
    with mock.patch.object(github, 'requests_get') as mock_get:
        assert github._fetch_latest_release_tag() == 'v2026.5.0'
    mock_get.assert_not_called()


def test_fetch_latest_release_tag_backoff_skips_fetch(
    github_env: None, redis_data: dict[str, str]
) -> None:
    redis_data['github-api-error'] = 'something'
    with mock.patch.object(github, 'requests_get') as mock_get:
        assert github._fetch_latest_release_tag() is None
    mock_get.assert_not_called()


def test_fetch_latest_release_tag_happy_path(
    github_env: None, redis_data: dict[str, str]
) -> None:
    resp = _resp(200, json_data={'tag_name': 'v2026.6.0', 'name': 'Release'})
    with mock.patch.object(
        github, 'requests_get', return_value=resp
    ) as mock_get:
        assert github._fetch_latest_release_tag() == 'v2026.6.0'
    assert redis_data[github.LATEST_RELEASE_TAG_KEY] == 'v2026.6.0'
    url = mock_get.call_args.args[0]
    assert url.endswith('/repos/Screenly/Anthias/releases/latest')


def test_fetch_latest_release_tag_request_exception(
    github_env: None, redis_data: dict[str, str]
) -> None:
    with mock.patch.object(
        github,
        'requests_get',
        side_effect=requests_exceptions.ConnectionError(),
    ):
        assert github._fetch_latest_release_tag() is None
    assert 'github-api-error' in redis_data


def test_fetch_latest_release_tag_5xx_triggers_backoff(
    github_env: None, redis_data: dict[str, str]
) -> None:
    with mock.patch.object(github, 'requests_get', return_value=_resp(500)):
        assert github._fetch_latest_release_tag() is None
    assert 'github-api-error' in redis_data


def test_fetch_latest_release_tag_invalid_json(
    github_env: None, redis_data: dict[str, str]
) -> None:
    resp = _resp(200)
    resp.json.side_effect = ValueError('bad json')
    with mock.patch.object(github, 'requests_get', return_value=resp):
        assert github._fetch_latest_release_tag() is None
    # Malformed bodies arm the same backoff as transport failures so
    # the next page render doesn't re-fetch immediately.
    assert 'github-api-error' in redis_data
    assert github.LATEST_RELEASE_TAG_KEY not in redis_data


def test_fetch_latest_release_tag_missing_tag_name(
    github_env: None, redis_data: dict[str, str]
) -> None:
    resp = _resp(200, json_data={'name': 'Release without tag_name'})
    with mock.patch.object(github, 'requests_get', return_value=resp):
        assert github._fetch_latest_release_tag() is None
    assert 'github-api-error' in redis_data
    assert github.LATEST_RELEASE_TAG_KEY not in redis_data


def test_fetch_latest_release_tag_non_string_tag_name(
    github_env: None, redis_data: dict[str, str]
) -> None:
    resp = _resp(200, json_data={'tag_name': 12345})
    with mock.patch.object(github, 'requests_get', return_value=resp):
        assert github._fetch_latest_release_tag() is None
    assert 'github-api-error' in redis_data
    assert github.LATEST_RELEASE_TAG_KEY not in redis_data


def test_fetch_latest_release_tag_unparseable_tag_name(
    github_env: None, redis_data: dict[str, str]
) -> None:
    """An upstream tag like ``nightly`` must not be cached: with a
    24h TTL, caching it would pin is_up_to_date() to the fallback
    verdict for a day even after upstream corrects the tag. Trip the
    5-minute backoff instead so the next attempt re-fetches once
    upstream is fixed."""
    resp = _resp(200, json_data={'tag_name': 'nightly'})
    with mock.patch.object(github, 'requests_get', return_value=resp):
        assert github._fetch_latest_release_tag() is None
    assert 'github-api-error' in redis_data
    assert github.LATEST_RELEASE_TAG_KEY not in redis_data


# ---------------------------------------------------------------------------
# _parse_version
# ---------------------------------------------------------------------------


def test_parse_version_strips_leading_v() -> None:
    a = github._parse_version('v2026.5.0')
    b = github._parse_version('2026.5.0')
    assert a is not None and b is not None
    assert a == b


def test_parse_version_invalid_returns_none() -> None:
    assert github._parse_version('') is None
    assert github._parse_version('not-a-version') is None


def test_parse_version_calver_ordering_is_numeric() -> None:
    """Catches the bug from the issue: string compare would put 10
    before 5; packaging.version must compare numerically."""
    assert github._parse_version('2026.10.0') > github._parse_version(  # type: ignore[operator]
        '2026.5.0'
    )


# ---------------------------------------------------------------------------
# is_up_to_date
# ---------------------------------------------------------------------------


def test_is_up_to_date_matching_versions_returns_true(
    github_env: None, redis_data: dict[str, str]
) -> None:
    with (
        mock.patch.object(
            github, 'get_anthias_release', return_value='2026.5.0'
        ),
        mock.patch.object(
            github, '_fetch_latest_release_tag', return_value='v2026.5.0'
        ),
    ):
        assert github.is_up_to_date() is True
    assert redis_data[github._verdict_cache_key('2026.5.0')] == '1'


def test_is_up_to_date_local_behind_returns_false(
    github_env: None, redis_data: dict[str, str]
) -> None:
    with (
        mock.patch.object(
            github, 'get_anthias_release', return_value='2026.5.0'
        ),
        mock.patch.object(
            github, '_fetch_latest_release_tag', return_value='v2026.6.0'
        ),
    ):
        assert github.is_up_to_date() is False
    assert redis_data[github._verdict_cache_key('2026.5.0')] == '0'


def test_is_up_to_date_local_ahead_returns_true(
    github_env: None, redis_data: dict[str, str]
) -> None:
    """A local dev bump (e.g. master tip past the last release tag)
    is still 'up to date' — the indicator is about being behind, not
    matching exactly."""
    with (
        mock.patch.object(
            github, 'get_anthias_release', return_value='2026.10.0'
        ),
        mock.patch.object(
            github, '_fetch_latest_release_tag', return_value='v2026.5.0'
        ),
    ):
        assert github.is_up_to_date() is True
    assert redis_data[github._verdict_cache_key('2026.10.0')] == '1'


def test_is_up_to_date_unparseable_local_suppresses_indicator(
    github_env: None,
) -> None:
    """Dev builds without a parseable CalVer get no comparison and no
    pill. The remote fetch isn't even attempted in that case."""
    with (
        mock.patch.object(github, 'get_anthias_release', return_value=''),
        mock.patch.object(github, '_fetch_latest_release_tag') as fetch_mock,
    ):
        assert github.is_up_to_date() is True
    fetch_mock.assert_not_called()


def test_is_up_to_date_unparseable_local_string_suppresses_indicator(
    github_env: None,
) -> None:
    with (
        mock.patch.object(
            github, 'get_anthias_release', return_value='dev-snapshot'
        ),
        mock.patch.object(github, '_fetch_latest_release_tag') as fetch_mock,
    ):
        assert github.is_up_to_date() is True
    fetch_mock.assert_not_called()


def test_is_up_to_date_github_error_with_cached_verdict_uses_it(
    github_env: None, redis_data: dict[str, str]
) -> None:
    redis_data[github._verdict_cache_key('2026.5.0')] = '1'
    with (
        mock.patch.object(
            github, 'get_anthias_release', return_value='2026.5.0'
        ),
        mock.patch.object(
            github, '_fetch_latest_release_tag', return_value=None
        ),
    ):
        assert github.is_up_to_date() is True

    redis_data[github._verdict_cache_key('2026.5.0')] = '0'
    with (
        mock.patch.object(
            github, 'get_anthias_release', return_value='2026.5.0'
        ),
        mock.patch.object(
            github, '_fetch_latest_release_tag', return_value=None
        ),
    ):
        assert github.is_up_to_date() is False


def test_is_up_to_date_verdict_cache_does_not_leak_across_releases(
    github_env: None, redis_data: dict[str, str]
) -> None:
    """An upgrade during a GitHub outage must not reuse the previous
    version's verdict — that verdict was computed against the OLD
    installed release, so it can be stale either way after an
    upgrade. Without a cached verdict for the new release, fall back
    to False so the indicator state catches up to reality on the next
    successful check."""
    redis_data[github._verdict_cache_key('2026.5.0')] = '0'
    with (
        mock.patch.object(
            github, 'get_anthias_release', return_value='2026.6.0'
        ),
        mock.patch.object(
            github, '_fetch_latest_release_tag', return_value=None
        ),
    ):
        assert github.is_up_to_date() is False


def test_is_up_to_date_github_error_no_cache_returns_false(
    github_env: None,
) -> None:
    """First-run fail-pessimistic: don't claim 'up to date' when we
    have never successfully checked."""
    with (
        mock.patch.object(
            github, 'get_anthias_release', return_value='2026.5.0'
        ),
        mock.patch.object(
            github, '_fetch_latest_release_tag', return_value=None
        ),
    ):
        assert github.is_up_to_date() is False


def test_is_up_to_date_malformed_remote_tag_falls_back(
    github_env: None, redis_data: dict[str, str]
) -> None:
    redis_data[github._verdict_cache_key('2026.5.0')] = '1'
    with (
        mock.patch.object(
            github, 'get_anthias_release', return_value='2026.5.0'
        ),
        mock.patch.object(
            github,
            '_fetch_latest_release_tag',
            return_value='not-a-version',
        ),
    ):
        assert github.is_up_to_date() is True


def test_is_up_to_date_malformed_remote_tag_no_cache_returns_false(
    github_env: None,
) -> None:
    with (
        mock.patch.object(
            github, 'get_anthias_release', return_value='2026.5.0'
        ),
        mock.patch.object(
            github,
            '_fetch_latest_release_tag',
            return_value='not-a-version',
        ),
    ):
        assert github.is_up_to_date() is False


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
