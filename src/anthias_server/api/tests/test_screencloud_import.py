"""Unit tests for the ScreenCloud (GraphQL) import provider.

Never touches the network: the provider's module-level ``_session`` is
patched so each GraphQL query and file download is driven deterministically
— token validation, region parsing, file/link listing (with the
``linkType`` and mimetype filters), and per-item import.
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
from anthias_server.lib.integrations import graphql, screencloud
from anthias_server.lib.integrations.registry import (
    get_provider,
    list_provider_meta,
)
from anthias_server.settings import settings

PROVIDER = screencloud.ScreenCloudProvider()


@pytest.fixture(autouse=True)
def _clear_endpoint_cache() -> Any:
    # The region cache is module-global; clear it so tests reusing the same
    # token don't see a previously-resolved endpoint.
    screencloud._endpoint_cache.clear()
    yield
    screencloud._endpoint_cache.clear()


def _router(routes: dict[str, MagicMock]) -> Any:
    """Route a GraphQL POST to a canned response by query substring.

    The region-detection probe (``currentOrg``) always resolves to a valid
    org so ``_resolve`` picks the first region and the routed queries run.
    """

    def _post(url: str, **kwargs: Any) -> MagicMock:
        query = kwargs['json']['query']
        if 'currentOrg' in query:
            return _gql(200, data={'currentOrg': {'id': 'x'}})
        for needle, resp in routes.items():
            if needle in query:
                return resp
        return _gql(200, data={})

    return _post


class TestAuthAndSession:
    def test_bearer_header(self) -> None:
        assert graphql.bearer_headers('abc') == {
            'Authorization': 'Bearer abc',
            'Content-Type': 'application/json',
        }

    def test_session_not_anthias_ua(self) -> None:
        ua = screencloud._session.headers['User-Agent']
        assert isinstance(ua, str)
        assert not ua.startswith('Anthias/')
        assert 'anthias' not in ua.lower()


class TestTokenRegion:
    def test_no_prefix_means_autodetect(self) -> None:
        # No explicit region → region is None (auto-detected by probing).
        assert screencloud._split_region('abc') == (None, 'abc')

    def test_explicit_region_prefix(self) -> None:
        assert screencloud._split_region('us:secret') == ('us', 'secret')

    @patch('anthias_server.lib.integrations.screencloud._session.post')
    def test_resolve_detects_region(self, post_mock: MagicMock) -> None:
        # First region probed that returns currentOrg wins; endpoint host
        # encodes the region.
        post_mock.return_value = _gql(200, data={'currentOrg': {'id': 'x'}})
        resolved = screencloud._resolve('secret')
        assert resolved is not None
        endpoint, bearer = resolved
        assert bearer == 'secret'
        assert 'screencloud.com/graphql' in endpoint

    @patch('anthias_server.lib.integrations.screencloud._session.post')
    def test_resolve_none_when_no_region_accepts(
        self, post_mock: MagicMock
    ) -> None:
        post_mock.return_value = _gql(401)
        assert screencloud._resolve('secret') is None

    @patch('anthias_server.lib.integrations.screencloud._session.post')
    def test_explicit_prefix_overrides_cached_region(
        self, post_mock: MagicMock
    ) -> None:
        # A cached auto-detected region must not win over an explicit prefix.
        screencloud._endpoint_cache[screencloud._cache_key('secret')] = (
            screencloud._REGION_ENDPOINTS['eu']
        )
        post_mock.return_value = _gql(200, data={'currentOrg': {'id': 'x'}})
        resolved = screencloud._resolve('us:secret')
        assert resolved is not None
        assert '.us.' in resolved[0]


class TestValidateToken:
    @patch('anthias_server.lib.integrations.screencloud._session.post')
    def test_true_on_current_org(self, post_mock: MagicMock) -> None:
        post_mock.return_value = _gql(200, data={'currentOrg': {'id': 'x'}})
        assert PROVIDER.validate_token('tok') is True

    @patch('anthias_server.lib.integrations.screencloud._session.post')
    def test_false_on_errors(self, post_mock: MagicMock) -> None:
        post_mock.return_value = _gql(200, errors=[{'message': 'bad token'}])
        assert PROVIDER.validate_token('tok') is False

    @patch('anthias_server.lib.integrations.screencloud._session.post')
    def test_false_on_401(self, post_mock: MagicMock) -> None:
        post_mock.return_value = _gql(401)
        assert PROVIDER.validate_token('tok') is False

    @patch('anthias_server.lib.integrations.screencloud._session.post')
    def test_raises_on_5xx(self, post_mock: MagicMock) -> None:
        post_mock.return_value = _gql(503)
        with pytest.raises(requests.HTTPError):
            PROVIDER.validate_token('tok')


class TestListMedia:
    @patch('anthias_server.lib.integrations.screencloud._session.post')
    def test_files_and_links_filtered(self, post_mock: MagicMock) -> None:
        files = _gql(
            200,
            data={
                'allFiles': {
                    'pageInfo': {'hasNextPage': False, 'endCursor': None},
                    'nodes': [
                        {'id': 'f1', 'name': 'Pic', 'mimetype': 'image/png'},
                        {'id': 'f2', 'name': 'Clip', 'mimetype': 'video/mp4'},
                        {'id': 'f3', 'name': 'Song', 'mimetype': 'audio/mp3'},
                    ],
                }
            },
        )
        links = _gql(
            200,
            data={
                'allLinks': {
                    'pageInfo': {'hasNextPage': False, 'endCursor': None},
                    'nodes': [
                        {'id': 'l1', 'name': 'Site', 'linkType': 'STANDARD'},
                        {'id': 'l2', 'name': 'Dash', 'linkType': 'INTERNAL'},
                    ],
                }
            },
        )
        post_mock.side_effect = _router({'allFiles': files, 'allLinks': links})

        items = PROVIDER.list_media('tok')
        by_id = {item.remote_id: item for item in items}

        assert by_id['file:f1'].media_type == 'image'
        assert by_id['file:f1'].importable
        assert by_id['file:f2'].media_type == 'video'
        assert by_id['file:f2'].importable
        # Audio isn't supported.
        assert by_id['file:f3'].importable is False
        # Only STANDARD links import; INTERNAL is filtered out.
        assert by_id['link:l1'].importable is True
        assert by_id['link:l2'].importable is False
        assert by_id['link:l2'].skip_reason

    @patch('anthias_server.lib.integrations.screencloud._session.post')
    def test_graphql_error_surfaces_as_transport_error(
        self, post_mock: MagicMock
    ) -> None:
        # A GraphQL errors[] during listing must become a RequestException
        # so the API view returns a controlled 502, not a 500.
        post_mock.return_value = _gql(200, errors=[{'message': 'boom'}])
        with pytest.raises(requests.RequestException):
            PROVIDER.list_media('tok')


class TestHelpers:
    def test_media_type_from_mimetype(self) -> None:
        assert screencloud._file_media_type('image/png') == 'image'
        assert screencloud._file_media_type('video/mp4') == 'video'
        assert screencloud._file_media_type('audio/mp3') == 'audio'
        assert screencloud._file_media_type('application/pdf') == 'document'

    def test_download_url_prefers_file_source(self) -> None:
        file_obj = {
            'source': 'https://media.eu.screencloud.com/orig.jpg',
            'fileOutputsByFileId': {
                'nodes': [{'url': 'https://cdn/thumb.jpg'}]
            },
        }
        assert (
            screencloud._file_download_url(file_obj)
            == 'https://media.eu.screencloud.com/orig.jpg'
        )

    def test_download_url_falls_back_to_outputs(self) -> None:
        file_obj = {
            'source': None,
            'fileOutputsByFileId': {
                'nodes': [{'url': 'https://cdn/rendition.mp4'}]
            },
        }
        assert (
            screencloud._file_download_url(file_obj)
            == 'https://cdn/rendition.mp4'
        )


class TestRegistry:
    def test_registered(self) -> None:
        assert isinstance(
            get_provider('screencloud'), screencloud.ScreenCloudProvider
        )
        keys = {meta['key'] for meta in list_provider_meta()}
        assert 'screencloud' in keys


@pytest.mark.django_db
class TestImportItem:
    def test_idempotent_reimport_skips(self) -> None:
        Asset.objects.create(
            asset_id='sc-existing',
            name='Existing',
            uri='https://example.com/',
            mimetype='webpage',
            duration=10,
            metadata={
                'import_source': {
                    'provider': 'screencloud',
                    'remote_id': 'link:l1',
                }
            },
        )
        with patch(
            'anthias_server.lib.integrations.screencloud._session.post'
        ) as post_mock:
            outcome = PROVIDER.import_item('tok', 'link:l1')
        post_mock.assert_not_called()
        assert outcome.skipped is True and outcome.success is True

    def test_unrecognised_id_skipped(self) -> None:
        outcome = PROVIDER.import_item('tok', 'bogus:1')
        assert outcome.skipped is True

    @patch(
        'anthias_server.api.serializers.mixins.url_fails', return_value=False
    )
    @patch('anthias_server.lib.integrations.screencloud._session.post')
    def test_import_standard_link(
        self, post_mock: MagicMock, _url_fails: MagicMock
    ) -> None:
        post_mock.side_effect = _router(
            {
                'linkById': _gql(
                    200,
                    data={
                        'linkById': {
                            'id': 'l1',
                            'name': 'Wireload',
                            'url': 'https://wireload.net/',
                            'linkType': 'STANDARD',
                        }
                    },
                )
            }
        )
        outcome = PROVIDER.import_item('tok', 'link:l1', enable=True)
        assert outcome.success is True and not outcome.skipped

        asset = Asset.objects.get(asset_id=outcome.asset_id)
        assert asset.mimetype == 'webpage'
        assert asset.uri == 'https://wireload.net/'
        assert asset.metadata['import_source'] == {
            'provider': 'screencloud',
            'remote_id': 'link:l1',
        }

    @patch('anthias_server.lib.integrations.screencloud._session.post')
    def test_import_internal_link_skipped(self, post_mock: MagicMock) -> None:
        post_mock.side_effect = _router(
            {
                'linkById': _gql(
                    200,
                    data={
                        'linkById': {
                            'id': 'l2',
                            'name': 'Dashboard',
                            'url': 'https://studio.internal/x',
                            'linkType': 'INTERNAL',
                        }
                    },
                )
            }
        )
        outcome = PROVIDER.import_item('tok', 'link:l2')
        assert outcome.skipped is True
        assert Asset.objects.count() == 0

    @patch('anthias_server.lib.integrations.screencloud._session.get')
    @patch('anthias_server.lib.integrations.screencloud._session.post')
    def test_import_file_image_downloads_without_auth(
        self,
        post_mock: MagicMock,
        get_mock: MagicMock,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(settings, 'assetdir', str(tmp_path))
        post_mock.side_effect = _router(
            {
                'fileById': _gql(
                    200,
                    data={
                        'fileById': {
                            'id': 'f1',
                            'name': 'Pic',
                            'mimetype': 'image/png',
                            'availableAt': None,
                            'expireAt': None,
                            'source': 'https://media.us.screencloud.com/x.png',
                            'fileOutputsByFileId': {'nodes': []},
                        }
                    },
                )
            }
        )
        captured: dict[str, str] = {}

        def _get(url: str, **kwargs: Any) -> MagicMock:
            captured.update(kwargs.get('headers') or {})
            return _stream(200, [b'\x89PNG\r\n'])

        get_mock.side_effect = _get

        outcome = PROVIDER.import_item('tok', 'file:f1', enable=False)
        assert outcome.success is True and not outcome.skipped

        asset = Asset.objects.get(asset_id=outcome.asset_id)
        assert asset.mimetype == 'image'
        assert (tmp_path / f'{asset.asset_id}.png').is_file()
        # Pre-signed CDN original — the bearer token must NOT be sent.
        assert 'Authorization' not in captured

    @patch('anthias_server.lib.integrations.screencloud._session.post')
    def test_import_audio_file_skipped(self, post_mock: MagicMock) -> None:
        post_mock.side_effect = _router(
            {
                'fileById': _gql(
                    200,
                    data={
                        'fileById': {
                            'id': 'f3',
                            'name': 'Song',
                            'mimetype': 'audio/mp3',
                            'fileOutputsByFileId': {'nodes': []},
                        }
                    },
                )
            }
        )
        outcome = PROVIDER.import_item('tok', 'file:f3')
        assert outcome.skipped is True
