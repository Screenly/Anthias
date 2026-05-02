import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from raspberry_pi_imager.build_pi_imager_json import (
    MAINTENANCE_SUFFIX,
    REQUIRED_FIELDS,
    SUPPORTED_BOARDS,
    build_imager_json,
    get_asset_list,
    get_board_from_url,
    retrieve_and_patch_json,
)

RELEASE_TAG = 'v0.20.0'
BASE_RELEASE_URL = (
    'https://github.com/Screenly/Anthias/releases/download/' + RELEASE_TAG
)

MOCK_RELEASE_ASSETS = {
    'assets': [
        {
            'browser_download_url': (
                f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi1.img.zst'
            )
        },
        {
            'browser_download_url': (
                f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi2.img.zst'
            )
        },
        {
            'browser_download_url': (
                f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi3.img.zst'
            )
        },
        {
            'browser_download_url': (
                f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi4-64.img.zst'
            )
        },
        {
            'browser_download_url': (
                f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi5.img.zst'
            )
        },
        {
            'browser_download_url': (
                f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi2.img.zst.sha256'
            )
        },
        {
            'browser_download_url': (
                f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi2.json'
            )
        },
    ],
}


def make_image_metadata(board: str) -> dict[str, Any]:
    return {
        'name': f'Anthias ({board})',
        'description': 'Anthias digital signage',
        'icon': 'https://example.com/icon.svg',
        'website': 'https://anthias.screenly.io',
        'extract_size': '5951425536',
        'extract_sha256': 'abc123',
        'image_download_size': '1600981967',
        'image_download_sha256': 'def456',
        'release_date': '2025-01-01',
    }


def _build_side_effect(payload: Any) -> MagicMock:
    mock = MagicMock()
    mock.json.return_value = payload
    return mock


@pytest.fixture
def mock_requests_get() -> Iterator[MagicMock]:
    """Patches the module-level requests.get and yields the mock so each
    test can configure return_value or side_effect."""
    with patch(
        'raspberry_pi_imager.build_pi_imager_json.requests.get'
    ) as mock_get:
        yield mock_get


@pytest.fixture
def mock_release_assets(mock_requests_get: MagicMock) -> MagicMock:
    """A requests.get mock that always returns the canned release-assets
    payload — what get_asset_list() consumes."""
    mock_requests_get.return_value = _build_side_effect(MOCK_RELEASE_ASSETS)
    return mock_requests_get


@pytest.fixture
def mock_full_build(mock_requests_get: MagicMock) -> MagicMock:
    """A requests.get mock that wires up the three call shapes
    build_imager_json() makes: latest-release lookup, tag-asset list,
    and per-asset metadata json."""

    def side_effect(url: str, **kwargs: object) -> MagicMock:
        if 'releases/latest' in url:
            return _build_side_effect({'tag_name': RELEASE_TAG})
        if f'releases/tags/{RELEASE_TAG}' in url:
            return _build_side_effect(MOCK_RELEASE_ASSETS)
        for board in SUPPORTED_BOARDS:
            if board in url:
                return _build_side_effect(make_image_metadata(board))
        return _build_side_effect({})

    mock_requests_get.side_effect = side_effect
    return mock_requests_get


# ---------------------------------------------------------------------------
# get_board_from_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'filename, expected_board',
    [
        ('2025-01-01-anthias-pi1.img.zst', 'pi1'),
        ('2025-01-01-anthias-pi2.img.zst', 'pi2'),
        ('2025-01-01-anthias-pi3.img.zst', 'pi3'),
        ('2025-01-01-anthias-pi4-64.img.zst', 'pi4-64'),
        ('2025-01-01-anthias-pi5.img.zst', 'pi5'),
    ],
)
def test_get_board_from_url_extracts_board(
    filename: str, expected_board: str
) -> None:
    assert (
        get_board_from_url(f'{BASE_RELEASE_URL}/{filename}') == expected_board
    )


@pytest.mark.parametrize(
    'url',
    [
        f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi2.json',
        f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi2.img.zst.sha256',
        'https://example.com/file.zst',
    ],
)
def test_get_board_from_url_returns_none_for_non_image(url: str) -> None:
    assert get_board_from_url(url) is None


# ---------------------------------------------------------------------------
# get_asset_list
# ---------------------------------------------------------------------------


def test_get_asset_list_filters_to_supported_boards(
    mock_release_assets: MagicMock,
) -> None:
    urls = get_asset_list(RELEASE_TAG)
    boards = {get_board_from_url(u) for u in urls}

    assert boards == SUPPORTED_BOARDS


def test_get_asset_list_excludes_pi1(
    mock_release_assets: MagicMock,
) -> None:
    urls = get_asset_list(RELEASE_TAG)

    assert all(get_board_from_url(u) != 'pi1' for u in urls)


def test_get_asset_list_excludes_non_zst(
    mock_release_assets: MagicMock,
) -> None:
    urls = get_asset_list(RELEASE_TAG)

    assert all(u.endswith('.zst') for u in urls)


# ---------------------------------------------------------------------------
# retrieve_and_patch_json
# ---------------------------------------------------------------------------


def test_retrieve_and_patch_json_patches_url(
    mock_requests_get: MagicMock,
) -> None:
    mock_requests_get.return_value = _build_side_effect(
        make_image_metadata('pi4-64')
    )
    url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi4-64.img.zst'

    assert retrieve_and_patch_json(url)['url'] == url


def test_retrieve_and_patch_json_converts_sizes_to_int(
    mock_requests_get: MagicMock,
) -> None:
    mock_requests_get.return_value = _build_side_effect(
        make_image_metadata('pi5')
    )
    url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi5.img.zst'

    result = retrieve_and_patch_json(url)

    assert isinstance(result['extract_size'], int)
    assert isinstance(result['image_download_size'], int)


@pytest.mark.parametrize('board', ['pi2', 'pi3'])
def test_retrieve_and_patch_json_marks_maintenance_boards(
    mock_requests_get: MagicMock, board: str
) -> None:
    mock_requests_get.return_value = _build_side_effect(
        make_image_metadata(board)
    )
    url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-{board}.img.zst'

    result = retrieve_and_patch_json(url)

    assert result['description'].endswith(MAINTENANCE_SUFFIX)


@pytest.mark.parametrize('board', ['pi4-64', 'pi5'])
def test_retrieve_and_patch_json_skips_maintenance_for_modern_boards(
    mock_requests_get: MagicMock, board: str
) -> None:
    mock_requests_get.return_value = _build_side_effect(
        make_image_metadata(board)
    )
    url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-{board}.img.zst'

    assert (
        'Maintenance mode' not in retrieve_and_patch_json(url)['description']
    )


def test_retrieve_and_patch_json_has_all_required_fields(
    mock_requests_get: MagicMock,
) -> None:
    mock_requests_get.return_value = _build_side_effect(
        make_image_metadata('pi5')
    )
    url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi5.img.zst'

    result = retrieve_and_patch_json(url)
    missing = REQUIRED_FIELDS - result.keys()

    assert not missing, f'Missing fields: {missing}'


# ---------------------------------------------------------------------------
# build_imager_json
# ---------------------------------------------------------------------------


def test_build_imager_json_emits_one_entry_per_supported_board(
    mock_full_build: MagicMock,
) -> None:
    result = build_imager_json()

    assert 'os_list' in result
    assert isinstance(result['os_list'], list)
    assert len(result['os_list']) == len(SUPPORTED_BOARDS)


def test_build_imager_json_round_trips_through_json(
    mock_full_build: MagicMock,
) -> None:
    result = build_imager_json()

    assert json.loads(json.dumps(result)) == result


def test_build_imager_json_excludes_pi1(mock_full_build: MagicMock) -> None:
    result = build_imager_json()

    assert all('(pi1)' not in entry['name'] for entry in result['os_list'])
