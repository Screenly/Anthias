"""Unit tests for the OptiSigns (GraphQL) import provider.

Never touches the network: the provider's module-level ``_session`` is
patched. Covers token validation, asset listing + classification (files
vs external web links vs apps/YouTube/internal content), and per-item
import.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from anthias_server.app.models import Asset
from anthias_server.api.tests._graphql_helpers import (
    gql_response as _gql,
    stream_response as _stream,
)
from anthias_server.lib.integrations import graphql, optisigns
from anthias_server.lib.integrations.registry import (
    get_provider,
    list_provider_meta,
)
from anthias_server.settings import settings

PROVIDER = optisigns.OptiSignsProvider()


def _assets(nodes: list[dict[str, Any]]) -> MagicMock:
    edges = [{'cursor': n['_id'], 'node': n} for n in nodes]
    return _gql(200, data={'assets': {'page': {'edges': edges}}})


def _router(
    by_id: dict[str, dict[str, Any]] | None = None,
    listing: MagicMock | None = None,
) -> Any:
    def _post(url: str, **kwargs: Any) -> MagicMock:
        query = kwargs['json']['query']
        variables = kwargs['json'].get('variables') or {}
        if '_id: $id' in query:
            node = (by_id or {}).get(str(variables.get('id') or ''))
            return _assets([node] if node else [])
        if '$first' in query:
            return listing if listing is not None else _assets([])
        return _gql(200, data={'assets': {'page': {'edges': []}}})

    return _post


class TestAuthAndSession:
    def test_bearer_header(self) -> None:
        assert graphql.bearer_headers('abc') == {
            'Authorization': 'Bearer abc',
            'Content-Type': 'application/json',
        }

    def test_session_not_anthias_ua(self) -> None:
        ua = optisigns._session.headers['User-Agent']
        assert not ua.startswith('Anthias/')
        assert 'anthias' not in ua.lower()


class TestValidateToken:
    @patch('anthias_server.lib.integrations.optisigns._session.post')
    def test_true(self, post_mock: MagicMock) -> None:
        post_mock.return_value = _assets([])
        assert PROVIDER.validate_token('tok') is True

    @patch('anthias_server.lib.integrations.optisigns._session.post')
    def test_false_on_errors(self, post_mock: MagicMock) -> None:
        post_mock.return_value = _gql(200, errors=[{'message': 'bad'}])
        assert PROVIDER.validate_token('tok') is False

    @patch('anthias_server.lib.integrations.optisigns._session.post')
    def test_false_on_401(self, post_mock: MagicMock) -> None:
        post_mock.return_value = _gql(401)
        assert PROVIDER.validate_token('tok') is False

    @patch('anthias_server.lib.integrations.optisigns._session.post')
    def test_raises_on_5xx(self, post_mock: MagicMock) -> None:
        post_mock.return_value = _gql(503)
        with pytest.raises(requests.HTTPError):
            PROVIDER.validate_token('tok')


class TestClassify:
    def test_image_and_video(self) -> None:
        assert optisigns._classify({'fileType': 'image'}) == 'image'
        assert optisigns._classify({'fileType': 'video'}) == 'video'

    def test_external_weblink_is_webpage(self) -> None:
        assert (
            optisigns._classify({'webLink': 'https://example.com/'})
            == 'webpage'
        )

    def test_youtube_and_internal_excluded(self) -> None:
        assert (
            optisigns._classify(
                {'webLink': 'https://youtube.com/watch', 'youtubeType': 'v'}
            )
            is None
        )
        assert (
            optisigns._classify({'webLink': 'https://app.optisigns.com/x'})
            is None
        )

    def test_app_without_media_excluded(self) -> None:
        assert optisigns._classify({'appType': 'dashboard'}) is None

    def test_file_download_url_prefers_video_1080p(self) -> None:
        node = {
            'fileType': 'video',
            'video_1080p': 'https://cdn/v.mp4',
            'path': 'https://cdn/orig',
        }
        assert optisigns._file_download_url(node) == 'https://cdn/v.mp4'

    def test_file_download_url_none_for_relative_path(self) -> None:
        assert (
            optisigns._file_download_url(
                {'fileType': 'image', 'path': '/relative/x.png'}
            )
            is None
        )


class TestListMedia:
    @patch('anthias_server.lib.integrations.optisigns._session.post')
    def test_classifies_and_filters(self, post_mock: MagicMock) -> None:
        post_mock.side_effect = _router(
            listing=_assets(
                [
                    {'_id': 'a1', 'name': 'Pic', 'fileType': 'image'},
                    {'_id': 'a2', 'name': 'Clip', 'fileType': 'video'},
                    {
                        '_id': 'a3',
                        'name': 'Site',
                        'webLink': 'https://example.com/',
                    },
                    {
                        '_id': 'a4',
                        'name': 'YT',
                        'webLink': 'https://youtube.com/w',
                        'youtubeType': 'v',
                    },
                    {'_id': 'a5', 'name': 'Dash', 'appType': 'dashboard'},
                    {
                        '_id': 'a6',
                        'name': 'Int',
                        'webLink': 'https://app.optisigns.com/x',
                    },
                ]
            )
        )
        items = PROVIDER.list_media('tok')
        by_id = {i.remote_id: i for i in items}
        assert by_id['a1'].media_type == 'image' and by_id['a1'].importable
        assert by_id['a2'].media_type == 'video' and by_id['a2'].importable
        assert by_id['a3'].media_type == 'webpage' and by_id['a3'].importable
        assert by_id['a4'].importable is False
        assert by_id['a5'].importable is False
        assert by_id['a6'].importable is False
        assert by_id['a4'].skip_reason

    @patch('anthias_server.lib.integrations.optisigns._session.post')
    def test_graphql_error_surfaces_as_transport_error(
        self, post_mock: MagicMock
    ) -> None:
        post_mock.return_value = _gql(200, errors=[{'message': 'boom'}])
        with pytest.raises(requests.RequestException):
            PROVIDER.list_media('tok')


class TestRegistry:
    def test_registered(self) -> None:
        assert isinstance(
            get_provider('optisigns'), optisigns.OptiSignsProvider
        )
        assert 'optisigns' in {m['key'] for m in list_provider_meta()}


@pytest.mark.django_db
class TestImportItem:
    def test_idempotent_reimport_skips(self) -> None:
        Asset.objects.create(
            asset_id='opti-existing',
            name='Existing',
            uri='https://example.com/',
            mimetype='webpage',
            duration=10,
            metadata={
                'import_source': {
                    'provider': 'optisigns',
                    'remote_id': 'a1',
                }
            },
        )
        with patch(
            'anthias_server.lib.integrations.optisigns._session.post'
        ) as post_mock:
            outcome = PROVIDER.import_item('tok', 'a1')
        post_mock.assert_not_called()
        assert outcome.skipped is True and outcome.success is True

    @patch(
        'anthias_server.api.serializers.mixins.url_fails', return_value=False
    )
    @patch('anthias_server.lib.integrations.optisigns._session.post')
    def test_import_external_webpage(
        self, post_mock: MagicMock, _url_fails: MagicMock
    ) -> None:
        post_mock.side_effect = _router(
            by_id={
                'a3': {
                    '_id': 'a3',
                    'name': 'Wireload',
                    'webLink': 'https://wireload.net/',
                    'duration': 12,
                }
            }
        )
        outcome = PROVIDER.import_item('tok', 'a3', enable=True)
        assert outcome.success is True and not outcome.skipped
        asset = Asset.objects.get(asset_id=outcome.asset_id)
        assert asset.mimetype == 'webpage'
        assert asset.uri == 'https://wireload.net/'
        assert asset.metadata['import_source'] == {
            'provider': 'optisigns',
            'remote_id': 'a3',
        }

    @patch('anthias_server.lib.integrations.optisigns._session.get')
    @patch('anthias_server.lib.integrations.optisigns._session.post')
    def test_import_image_downloads_without_auth(
        self,
        post_mock: MagicMock,
        get_mock: MagicMock,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(settings, 'assetdir', str(tmp_path))
        post_mock.side_effect = _router(
            by_id={
                'a1': {
                    '_id': 'a1',
                    'name': 'Pic',
                    'fileType': 'image',
                    'fileExtension': 'png',
                    'path': 'https://cdn.optisigns.com/x.png',
                }
            }
        )
        captured: dict[str, str] = {}

        def _get(url: str, **kwargs: Any) -> MagicMock:
            captured.update(kwargs.get('headers') or {})
            return _stream(200, [b'\x89PNG\r\n'])

        get_mock.side_effect = _get

        outcome = PROVIDER.import_item('tok', 'a1', enable=False)
        assert outcome.success is True and not outcome.skipped
        asset = Asset.objects.get(asset_id=outcome.asset_id)
        assert asset.mimetype == 'image'
        assert (tmp_path / f'{asset.asset_id}.png').is_file()
        assert 'Authorization' not in captured

    @patch('anthias_server.lib.integrations.optisigns._session.post')
    def test_import_relative_path_skipped(self, post_mock: MagicMock) -> None:
        post_mock.side_effect = _router(
            by_id={
                'a1': {
                    '_id': 'a1',
                    'name': 'Pic',
                    'fileType': 'image',
                    'path': '/relative/x.png',
                }
            }
        )
        outcome = PROVIDER.import_item('tok', 'a1')
        assert outcome.skipped is True
        assert 're-upload' in (outcome.reason or '')

    @patch('anthias_server.lib.integrations.optisigns._session.post')
    def test_import_youtube_skipped(self, post_mock: MagicMock) -> None:
        post_mock.side_effect = _router(
            by_id={
                'a4': {
                    '_id': 'a4',
                    'name': 'YT',
                    'webLink': 'https://youtube.com/watch?v=x',
                    'youtubeType': 'video',
                }
            }
        )
        outcome = PROVIDER.import_item('tok', 'a4')
        assert outcome.skipped is True
