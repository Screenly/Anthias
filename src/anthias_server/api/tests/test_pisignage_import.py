"""Unit tests for the piSignage (REST) import provider.

Never touches the network: the provider's module-level ``_session`` is
patched. Covers credential parsing, login/validation, file listing +
classification, and per-item import (with the token attached to the
same-host media download).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import unquote

import pytest
import requests

from anthias_server.app.models import Asset
from anthias_server.api.tests._graphql_helpers import (
    json_response as _resp,
    stream_response as _stream,
)
from anthias_server.lib.integrations import pisignage
from anthias_server.lib.integrations.base import ProviderImportError
from anthias_server.lib.integrations.registry import (
    get_provider,
    list_provider_meta,
)
from anthias_server.settings import settings

PROVIDER = pisignage.PiSignageProvider()
TOKEN = 'mysite:user@example.com:secret'


def _login_ok() -> MagicMock:
    return _resp(200, {'token': 'jwt-123'})


def _get_router(
    *,
    listing: MagicMock | None = None,
    details: dict[str, MagicMock] | None = None,
    download: MagicMock | None = None,
) -> Any:
    def _get(url: str, **kwargs: Any) -> MagicMock:
        if kwargs.get('stream'):
            return download or _stream(200, [b'\x89PNG'])
        if url.endswith('/api/files'):
            return listing or _resp(200, {'data': {}})
        name = unquote(url.rsplit('/api/files/', 1)[-1])
        return (details or {}).get(name) or _resp(404, {})

    return _get


class TestTokenAndSession:
    def test_parse_token(self) -> None:
        assert pisignage._parse_token('sub:user@x.com:p:w') == (
            'sub',
            'user@x.com',
            'p:w',
        )

    def test_parse_token_malformed(self) -> None:
        with pytest.raises(ProviderImportError):
            pisignage._parse_token('just-one-part')

    def test_session_not_anthias_ua(self) -> None:
        ua = pisignage._session.headers['User-Agent']
        assert not ua.startswith('Anthias/')
        assert 'anthias' not in ua.lower()


class TestValidateToken:
    @patch('anthias_server.lib.integrations.pisignage._session.post')
    def test_true(self, post_mock: MagicMock) -> None:
        post_mock.return_value = _login_ok()
        assert PROVIDER.validate_token(TOKEN) is True

    @patch('anthias_server.lib.integrations.pisignage._session.post')
    def test_false_on_401(self, post_mock: MagicMock) -> None:
        post_mock.return_value = _resp(401, {})
        assert PROVIDER.validate_token(TOKEN) is False

    def test_false_on_malformed(self) -> None:
        assert PROVIDER.validate_token('nope') is False

    @patch('anthias_server.lib.integrations.pisignage._session.post')
    def test_raises_on_5xx(self, post_mock: MagicMock) -> None:
        post_mock.return_value = _resp(503)
        with pytest.raises(requests.HTTPError):
            PROVIDER.validate_token(TOKEN)


class TestHelpers:
    def test_map_type(self) -> None:
        assert pisignage._map_type('image/png') == 'image'
        assert pisignage._map_type('video') == 'video'
        assert pisignage._map_type('audio/mp3') == 'audio'
        assert pisignage._map_type(None) is None

    def test_type_from_name(self) -> None:
        assert pisignage._type_from_name('a.PNG') == 'image'
        assert pisignage._type_from_name('b.mp4') == 'video'
        assert pisignage._type_from_name('c.pdf') is None


class TestListMedia:
    @patch('anthias_server.lib.integrations.pisignage._session.get')
    @patch('anthias_server.lib.integrations.pisignage._session.post')
    def test_classifies_files(
        self, post_mock: MagicMock, get_mock: MagicMock
    ) -> None:
        post_mock.return_value = _login_ok()
        get_mock.side_effect = _get_router(
            listing=_resp(
                200,
                {
                    'data': {
                        'dbdata': [
                            {'name': 'pic.png', 'type': 'image'},
                            {'name': 'clip.mp4', 'type': 'video'},
                            {'name': 'song.mp3', 'type': 'audio'},
                        ],
                        'files': ['extra.jpg'],
                    }
                },
            )
        )
        items = PROVIDER.list_media(TOKEN)
        by_id = {i.remote_id: i for i in items}
        assert by_id['pic.png'].media_type == 'image'
        assert by_id['pic.png'].importable
        assert by_id['clip.mp4'].importable
        assert by_id['song.mp3'].importable is False
        # ``files``-only entry classified by extension.
        assert by_id['extra.jpg'].media_type == 'image'

    @patch('anthias_server.lib.integrations.pisignage._session.post')
    def test_bad_credentials_surface_as_transport_error(
        self, post_mock: MagicMock
    ) -> None:
        post_mock.return_value = _resp(401, {})
        with pytest.raises(requests.RequestException):
            PROVIDER.list_media(TOKEN)


class TestRegistry:
    def test_registered(self) -> None:
        assert isinstance(
            get_provider('pisignage'), pisignage.PiSignageProvider
        )
        assert 'pisignage' in {m['key'] for m in list_provider_meta()}


@pytest.mark.django_db
class TestImportItem:
    def test_idempotent_reimport_skips(self) -> None:
        Asset.objects.create(
            asset_id='pi-existing',
            name='pic.png',
            uri='/data/x.png',
            mimetype='image',
            duration=10,
            metadata={
                'import_source': {
                    'provider': 'pisignage',
                    'remote_id': 'pic.png',
                }
            },
        )
        with patch(
            'anthias_server.lib.integrations.pisignage._session.post'
        ) as post_mock:
            outcome = PROVIDER.import_item(TOKEN, 'pic.png')
        post_mock.assert_not_called()
        assert outcome.skipped is True and outcome.success is True

    @patch('anthias_server.lib.integrations.pisignage._session.get')
    @patch('anthias_server.lib.integrations.pisignage._session.post')
    def test_import_image_attaches_token_on_same_host(
        self,
        post_mock: MagicMock,
        get_mock: MagicMock,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(settings, 'assetdir', str(tmp_path))
        post_mock.return_value = _login_ok()
        captured: dict[str, str] = {}
        detail = _resp(
            200,
            {
                'data': {
                    'name': 'pic.png',
                    'type': 'image',
                    'path': '/media/acct/pic.png',
                    'dbdata': {'duration': '15'},
                }
            },
        )

        def _get(url: str, **kwargs: Any) -> MagicMock:
            if kwargs.get('stream'):
                captured.update(kwargs.get('headers') or {})
                return _stream(200, [b'\x89PNG\r\n'])
            return detail

        get_mock.side_effect = _get

        outcome = PROVIDER.import_item(TOKEN, 'pic.png', enable=False)
        assert outcome.success is True and not outcome.skipped
        asset = Asset.objects.get(asset_id=outcome.asset_id)
        assert asset.mimetype == 'image'
        assert (tmp_path / f'{asset.asset_id}.png').is_file()
        # Media is on the piSignage host, so the token IS attached.
        assert captured.get('x-access-token') == 'jwt-123'

    @patch('anthias_server.lib.integrations.pisignage._session.get')
    @patch('anthias_server.lib.integrations.pisignage._session.post')
    def test_import_audio_skipped(
        self, post_mock: MagicMock, get_mock: MagicMock
    ) -> None:
        post_mock.return_value = _login_ok()
        get_mock.side_effect = _get_router(
            details={
                'song.mp3': _resp(
                    200,
                    {
                        'data': {
                            'name': 'song.mp3',
                            'type': 'audio',
                            'path': '/media/acct/song.mp3',
                        }
                    },
                )
            }
        )
        outcome = PROVIDER.import_item(TOKEN, 'song.mp3')
        assert outcome.skipped is True

    @patch('anthias_server.lib.integrations.pisignage._session.get')
    @patch('anthias_server.lib.integrations.pisignage._session.post')
    def test_import_missing_path_skipped(
        self, post_mock: MagicMock, get_mock: MagicMock
    ) -> None:
        post_mock.return_value = _login_ok()
        get_mock.side_effect = _get_router(
            details={
                'pic.png': _resp(
                    200,
                    {'data': {'name': 'pic.png', 'type': 'image'}},
                )
            }
        )
        outcome = PROVIDER.import_item(TOKEN, 'pic.png')
        assert outcome.skipped is True
