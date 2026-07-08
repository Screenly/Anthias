"""Unit tests for the Xibo (REST) import provider.

Never touches the network: the provider's module-level ``_session`` is
patched. Covers credential parsing, OAuth token exchange, library listing
+ classification, and per-item import (Bearer token attached to the
same-host download).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from anthias_server.app.models import Asset
from anthias_server.lib.integrations import xibo
from anthias_server.lib.integrations.base import ProviderImportError
from anthias_server.lib.integrations.registry import (
    get_provider,
    list_provider_meta,
)
from anthias_server.settings import settings

PROVIDER = xibo.XiboProvider()
TOKEN = 'cms.xibosignage.com:client123:secret456'


def _resp(status: int, json_body: Any = None) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.ok = 200 <= status < 400
    resp.json.return_value = json_body
    if not resp.ok:
        resp.raise_for_status.side_effect = requests.HTTPError(
            f'HTTP {status}', response=resp
        )
    return resp


def _stream(status: int, chunks: list[bytes]) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.ok = 200 <= status < 400
    resp.iter_content.return_value = iter(chunks)
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _token_ok() -> MagicMock:
    return _resp(200, {'access_token': 'bearer-xyz'})


class TestTokenAndSession:
    def test_parse_token(self) -> None:
        assert xibo._parse_token('h:cid:sec') == ('h', 'cid', 'sec')

    def test_parse_token_malformed(self) -> None:
        with pytest.raises(ProviderImportError):
            xibo._parse_token('only:two')

    def test_map_type(self) -> None:
        assert xibo._map_type('image') == 'image'
        assert xibo._map_type('video') == 'video'
        assert xibo._map_type('audio') is None
        assert xibo._map_type(None) is None


class TestValidateToken:
    @patch('anthias_server.lib.integrations.xibo._session.post')
    def test_true(self, post_mock: MagicMock) -> None:
        post_mock.return_value = _token_ok()
        assert PROVIDER.validate_token(TOKEN) is True

    @patch('anthias_server.lib.integrations.xibo._session.post')
    def test_false_on_bad_credentials(self, post_mock: MagicMock) -> None:
        post_mock.return_value = _resp(400, {'error': 'invalid_client'})
        assert PROVIDER.validate_token(TOKEN) is False

    def test_false_on_malformed(self) -> None:
        assert PROVIDER.validate_token('nope') is False

    @patch('anthias_server.lib.integrations.xibo._session.post')
    def test_raises_on_5xx(self, post_mock: MagicMock) -> None:
        post_mock.return_value = _resp(503)
        with pytest.raises(requests.HTTPError):
            PROVIDER.validate_token(TOKEN)


class TestListMedia:
    @patch('anthias_server.lib.integrations.xibo._session.get')
    @patch('anthias_server.lib.integrations.xibo._session.post')
    def test_classifies_library(
        self, post_mock: MagicMock, get_mock: MagicMock
    ) -> None:
        post_mock.return_value = _token_ok()
        get_mock.return_value = _resp(
            200,
            [
                {'mediaId': 1, 'name': 'Pic', 'mediaType': 'image'},
                {'mediaId': 2, 'name': 'Clip', 'mediaType': 'video'},
                {'mediaId': 3, 'name': 'Song', 'mediaType': 'audio'},
            ],
        )
        items = PROVIDER.list_media(TOKEN)
        by_id = {i.remote_id: i for i in items}
        assert by_id['1'].media_type == 'image' and by_id['1'].importable
        assert by_id['2'].importable
        assert by_id['3'].importable is False

    @patch('anthias_server.lib.integrations.xibo._session.post')
    def test_bad_credentials_surface_as_transport_error(
        self, post_mock: MagicMock
    ) -> None:
        post_mock.return_value = _resp(401, {})
        with pytest.raises(requests.RequestException):
            PROVIDER.list_media(TOKEN)


class TestRegistry:
    def test_registered(self) -> None:
        assert isinstance(get_provider('xibo'), xibo.XiboProvider)
        assert 'xibo' in {m['key'] for m in list_provider_meta()}


@pytest.mark.django_db
class TestImportItem:
    def test_idempotent_reimport_skips(self) -> None:
        Asset.objects.create(
            asset_id='xibo-existing',
            name='Pic',
            uri='/data/x.png',
            mimetype='image',
            duration=10,
            metadata={'import_source': {'provider': 'xibo', 'remote_id': '1'}},
        )
        with patch(
            'anthias_server.lib.integrations.xibo._session.post'
        ) as post_mock:
            outcome = PROVIDER.import_item(TOKEN, '1')
        post_mock.assert_not_called()
        assert outcome.skipped is True and outcome.success is True

    @patch('anthias_server.lib.integrations.xibo._session.get')
    @patch('anthias_server.lib.integrations.xibo._session.post')
    def test_import_image_attaches_bearer_on_same_host(
        self,
        post_mock: MagicMock,
        get_mock: MagicMock,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(settings, 'assetdir', str(tmp_path))
        post_mock.return_value = _token_ok()
        captured: dict[str, str] = {}

        def _get(url: str, **kwargs: Any) -> MagicMock:
            if kwargs.get('stream'):
                captured.update(kwargs.get('headers') or {})
                return _stream(200, [b'\x89PNG\r\n'])
            return _resp(
                200,
                [
                    {
                        'mediaId': 1,
                        'name': 'Pic',
                        'mediaType': 'image',
                        'fileName': 'pic.png',
                        'duration': 20,
                    }
                ],
            )

        get_mock.side_effect = _get

        outcome = PROVIDER.import_item(TOKEN, '1', enable=False)
        assert outcome.success is True and not outcome.skipped
        asset = Asset.objects.get(asset_id=outcome.asset_id)
        assert asset.mimetype == 'image'
        assert (tmp_path / f'{asset.asset_id}.png').is_file()
        # CMS-hosted download → the Bearer token IS attached.
        assert captured.get('Authorization') == 'Bearer bearer-xyz'

    @patch('anthias_server.lib.integrations.xibo._session.get')
    @patch('anthias_server.lib.integrations.xibo._session.post')
    def test_import_audio_skipped(
        self, post_mock: MagicMock, get_mock: MagicMock
    ) -> None:
        post_mock.return_value = _token_ok()
        get_mock.return_value = _resp(
            200, [{'mediaId': 3, 'name': 'Song', 'mediaType': 'audio'}]
        )
        outcome = PROVIDER.import_item(TOKEN, '3')
        assert outcome.skipped is True
