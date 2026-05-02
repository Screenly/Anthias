import json
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

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


class TestGetBoardFromUrl(unittest.TestCase):
    def test_pi2(self) -> None:
        url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi2.img.zst'
        self.assertEqual(get_board_from_url(url), 'pi2')

    def test_pi3(self) -> None:
        url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi3.img.zst'
        self.assertEqual(get_board_from_url(url), 'pi3')

    def test_pi4_64(self) -> None:
        url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi4-64.img.zst'
        self.assertEqual(get_board_from_url(url), 'pi4-64')

    def test_pi5(self) -> None:
        url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi5.img.zst'
        self.assertEqual(get_board_from_url(url), 'pi5')

    def test_pi1(self) -> None:
        url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi1.img.zst'
        self.assertEqual(get_board_from_url(url), 'pi1')

    def test_non_zst_returns_none(self) -> None:
        url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi2.json'
        self.assertIsNone(get_board_from_url(url))

    def test_sha256_returns_none(self) -> None:
        url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi2.img.zst.sha256'
        self.assertIsNone(get_board_from_url(url))

    def test_no_board_returns_none(self) -> None:
        self.assertIsNone(get_board_from_url('https://example.com/file.zst'))


class TestGetAssetList(unittest.TestCase):
    @patch('raspberry_pi_imager.build_pi_imager_json.requests.get')
    def test_filters_to_supported_boards(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = MOCK_RELEASE_ASSETS
        mock_get.return_value = mock_response

        urls = get_asset_list(RELEASE_TAG)
        boards = {get_board_from_url(u) for u in urls}

        self.assertEqual(boards, SUPPORTED_BOARDS)

    @patch('raspberry_pi_imager.build_pi_imager_json.requests.get')
    def test_excludes_pi1(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = MOCK_RELEASE_ASSETS
        mock_get.return_value = mock_response

        urls = get_asset_list(RELEASE_TAG)

        for url in urls:
            self.assertNotEqual(get_board_from_url(url), 'pi1')

    @patch('raspberry_pi_imager.build_pi_imager_json.requests.get')
    def test_excludes_non_zst(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = MOCK_RELEASE_ASSETS
        mock_get.return_value = mock_response

        urls = get_asset_list(RELEASE_TAG)

        for url in urls:
            self.assertTrue(url.endswith('.zst'))


class TestRetrieveAndPatchJson(unittest.TestCase):
    @patch('raspberry_pi_imager.build_pi_imager_json.requests.get')
    def test_patches_url(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = make_image_metadata('pi4-64')
        mock_get.return_value = mock_response

        url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi4-64.img.zst'
        result = retrieve_and_patch_json(url)

        self.assertEqual(result['url'], url)

    @patch('raspberry_pi_imager.build_pi_imager_json.requests.get')
    def test_converts_sizes_to_int(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = make_image_metadata('pi5')
        mock_get.return_value = mock_response

        url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi5.img.zst'
        result = retrieve_and_patch_json(url)

        self.assertIsInstance(result['extract_size'], int)
        self.assertIsInstance(result['image_download_size'], int)

    @patch('raspberry_pi_imager.build_pi_imager_json.requests.get')
    def test_maintenance_mode_pi2(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = make_image_metadata('pi2')
        mock_get.return_value = mock_response

        url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi2.img.zst'
        result = retrieve_and_patch_json(url)

        self.assertTrue(result['description'].endswith(MAINTENANCE_SUFFIX))

    @patch('raspberry_pi_imager.build_pi_imager_json.requests.get')
    def test_maintenance_mode_pi3(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = make_image_metadata('pi3')
        mock_get.return_value = mock_response

        url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi3.img.zst'
        result = retrieve_and_patch_json(url)

        self.assertTrue(result['description'].endswith(MAINTENANCE_SUFFIX))

    @patch('raspberry_pi_imager.build_pi_imager_json.requests.get')
    def test_no_maintenance_mode_pi5(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = make_image_metadata('pi5')
        mock_get.return_value = mock_response

        url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi5.img.zst'
        result = retrieve_and_patch_json(url)

        self.assertNotIn('Maintenance mode', result['description'])

    @patch('raspberry_pi_imager.build_pi_imager_json.requests.get')
    def test_has_all_required_fields(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = make_image_metadata('pi5')
        mock_get.return_value = mock_response

        url = f'{BASE_RELEASE_URL}/2025-01-01-anthias-pi5.img.zst'
        result = retrieve_and_patch_json(url)

        self.assertTrue(
            REQUIRED_FIELDS.issubset(result.keys()),
            f'Missing fields: {REQUIRED_FIELDS - result.keys()}',
        )


class TestBuildImagerJson(unittest.TestCase):
    @patch('raspberry_pi_imager.build_pi_imager_json.requests.get')
    def test_output_structure(self, mock_get: MagicMock) -> None:
        def side_effect(url: str, **kwargs: object) -> MagicMock:
            mock = MagicMock()
            if 'releases/latest' in url:
                mock.json.return_value = {'tag_name': RELEASE_TAG}
            elif f'releases/tags/{RELEASE_TAG}' in url:
                mock.json.return_value = MOCK_RELEASE_ASSETS
            else:
                for board in SUPPORTED_BOARDS:
                    if board in url:
                        mock.json.return_value = make_image_metadata(board)
                        break
            return mock

        mock_get.side_effect = side_effect

        result = build_imager_json()

        self.assertIn('os_list', result)
        self.assertIsInstance(result['os_list'], list)
        self.assertEqual(len(result['os_list']), len(SUPPORTED_BOARDS))

    @patch('raspberry_pi_imager.build_pi_imager_json.requests.get')
    def test_output_is_valid_json(self, mock_get: MagicMock) -> None:
        def side_effect(url: str, **kwargs: object) -> MagicMock:
            mock = MagicMock()
            if 'releases/latest' in url:
                mock.json.return_value = {'tag_name': RELEASE_TAG}
            elif f'releases/tags/{RELEASE_TAG}' in url:
                mock.json.return_value = MOCK_RELEASE_ASSETS
            else:
                for board in SUPPORTED_BOARDS:
                    if board in url:
                        mock.json.return_value = make_image_metadata(board)
                        break
            return mock

        mock_get.side_effect = side_effect

        result = build_imager_json()
        serialized = json.dumps(result)
        parsed = json.loads(serialized)

        self.assertEqual(result, parsed)

    @patch('raspberry_pi_imager.build_pi_imager_json.requests.get')
    def test_no_pi1_in_output(self, mock_get: MagicMock) -> None:
        def side_effect(url: str, **kwargs: object) -> MagicMock:
            mock = MagicMock()
            if 'releases/latest' in url:
                mock.json.return_value = {'tag_name': RELEASE_TAG}
            elif f'releases/tags/{RELEASE_TAG}' in url:
                mock.json.return_value = MOCK_RELEASE_ASSETS
            else:
                for board in SUPPORTED_BOARDS:
                    if board in url:
                        mock.json.return_value = make_image_metadata(board)
                        break
            return mock

        mock_get.side_effect = side_effect

        result = build_imager_json()

        for entry in result['os_list']:
            self.assertNotIn(
                '(pi1)', entry['name'], 'pi1 should not be in output'
            )


if __name__ == '__main__':
    unittest.main()
