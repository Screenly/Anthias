"""Unit tests for the Yodeck import provider + generic import endpoints.

These tests never reach the network. The Yodeck provider's module-level
``_session`` is patched on ``anthias_server.lib.integrations.yodeck`` so
each branch is driven deterministically — token validation, media
listing/pagination, field mapping, and the per-item import (webpage,
image download, idempotency, skips, cleanup-on-failure).

The HTTP-level tests use DRF's ``APIClient`` against the registered v2
routes with the provider layer mocked, so the serializer + view wiring
is under test independently of Yodeck's real API.
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from typing import Any
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from anthias_server.app.models import Asset
from anthias_server.api.tests._graphql_helpers import (
    json_response as _fake_response,
    stream_response as _fake_stream_response,
)
from anthias_server.lib.integrations import ingest, yodeck
from anthias_server.lib.integrations.base import (
    ImportOutcome,
    ProviderImportError,
    RemoteMediaItem,
)
from anthias_server.lib.integrations.registry import (
    get_provider,
    list_provider_meta,
)
from anthias_server.settings import settings


# Stateless provider (its HTTP session is module-level), so one shared
# instance is reused across tests. Keeping construction out of the
# ``pytest.raises`` blocks also means each of those blocks has a single
# throwing invocation (Sonar S5778).
PROVIDER = yodeck.YodeckProvider()


# ---------------------------------------------------------------------------
# Auth / session
# ---------------------------------------------------------------------------


class TestAuthAndSession:
    def test_auth_header_token_scheme(self) -> None:
        assert yodeck._auth_headers('label:secret') == {
            'Authorization': 'Token label:secret'
        }

    def test_session_does_not_use_anthias_user_agent(self) -> None:
        # Migration traffic hits a competitor's API; a self-identifying
        # ``Anthias/<ver>`` UA invites vendor-side blocking, so these
        # calls must NOT carry it.
        ua = yodeck._session.headers['User-Agent']
        assert isinstance(ua, str)
        assert not ua.startswith('Anthias/')
        assert 'anthias' not in ua.lower()


# ---------------------------------------------------------------------------
# validate_token
# ---------------------------------------------------------------------------


class TestValidateToken:
    @patch('anthias_server.lib.integrations.yodeck._session.get')
    def test_returns_true_on_200(self, get_mock: MagicMock) -> None:
        get_mock.return_value = _fake_response(200, {'results': []})
        assert PROVIDER.validate_token('good') is True
        _, kwargs = get_mock.call_args
        assert kwargs['headers']['Authorization'] == 'Token good'
        assert kwargs['params']['limit'] == 1

    @pytest.mark.parametrize('code', [401, 403])
    @patch('anthias_server.lib.integrations.yodeck._session.get')
    def test_returns_false_on_auth_failure(
        self, get_mock: MagicMock, code: int
    ) -> None:
        get_mock.return_value = _fake_response(code, {'detail': 'no'})
        assert PROVIDER.validate_token('bad') is False

    @patch('anthias_server.lib.integrations.yodeck._session.get')
    def test_raises_on_5xx(self, get_mock: MagicMock) -> None:
        get_mock.return_value = _fake_response(503)
        with pytest.raises(requests.HTTPError):
            PROVIDER.validate_token('x')

    @patch('anthias_server.lib.integrations.yodeck._session.get')
    def test_propagates_network_error(self, get_mock: MagicMock) -> None:
        get_mock.side_effect = requests.ConnectionError('boom')
        with pytest.raises(requests.ConnectionError):
            PROVIDER.validate_token('x')


# ---------------------------------------------------------------------------
# list_media
# ---------------------------------------------------------------------------


class TestListMedia:
    def _pages(
        self, pages_by_type: dict[str, dict[int, dict[str, Any]]]
    ) -> Any:
        """side_effect that serves list pages keyed on media_type+offset."""

        def _get(url: str, **kwargs: Any) -> MagicMock:
            params = kwargs['params']
            media_type = params['media_type']
            offset = params['offset']
            body = pages_by_type.get(media_type, {}).get(
                offset, {'results': [], 'next': None}
            )
            return _fake_response(200, body)

        return _get

    @patch('anthias_server.lib.integrations.yodeck._session.get')
    def test_maps_types_and_flags_unsupported(
        self, get_mock: MagicMock
    ) -> None:
        get_mock.side_effect = self._pages(
            {
                'image': {
                    0: {'results': [{'id': 1, 'name': 'Pic'}], 'next': None}
                },
                'video': {
                    0: {'results': [{'id': 2, 'name': 'Clip'}], 'next': None}
                },
                'webpage': {
                    0: {'results': [{'id': 3, 'name': 'Site'}], 'next': None}
                },
                'audio': {
                    0: {'results': [{'id': 4, 'name': 'Song'}], 'next': None}
                },
                'document': {
                    0: {'results': [{'id': 5, 'name': 'PDF'}], 'next': None}
                },
            }
        )
        items = PROVIDER.list_media('tok')
        by_id = {item.remote_id: item for item in items}

        assert by_id['1'].media_type == 'image' and by_id['1'].importable
        assert by_id['2'].media_type == 'video' and by_id['2'].importable
        assert by_id['3'].media_type == 'webpage' and by_id['3'].importable
        assert by_id['4'].importable is False
        assert by_id['4'].skip_reason
        assert by_id['5'].importable is False

    @patch('anthias_server.lib.integrations.yodeck._session.get')
    def test_follows_pagination(self, get_mock: MagicMock) -> None:
        get_mock.side_effect = self._pages(
            {
                'image': {
                    0: {
                        'results': [
                            {'id': 1, 'name': 'A'},
                            {'id': 2, 'name': 'B'},
                        ],
                        'next': 'https://x/?offset=2',
                    },
                    2: {'results': [{'id': 3, 'name': 'C'}], 'next': None},
                },
            }
        )
        items = PROVIDER.list_media('tok')
        image_ids = sorted(
            i.remote_id for i in items if i.media_type == 'image'
        )
        assert image_ids == ['1', '2', '3']

    @patch('anthias_server.lib.integrations.yodeck._session.get')
    def test_names_fallback_when_missing(self, get_mock: MagicMock) -> None:
        get_mock.side_effect = self._pages(
            {'image': {0: {'results': [{'id': 9}], 'next': None}}}
        )
        items = PROVIDER.list_media('tok')
        item = next(i for i in items if i.remote_id == '9')
        assert 'Yodeck media 9' == item.name


# ---------------------------------------------------------------------------
# Pure field-mapping helpers
# ---------------------------------------------------------------------------


class TestFieldMapping:
    def test_availability_window_uses_schedule(self) -> None:
        detail = {
            'availability_schedule': {
                'enable': True,
                'available_after': '2024-05-20T12:38:00Z',
                'available_before': '2024-06-21T12:38:00Z',
            }
        }
        start, end = yodeck._availability_window(detail)
        assert start == datetime(2024, 5, 20, 12, 38, tzinfo=timezone.utc)
        assert end == datetime(2024, 6, 21, 12, 38, tzinfo=timezone.utc)

    def test_availability_window_defaults_when_disabled(self) -> None:
        start, end = yodeck._availability_window({'availability_schedule': {}})
        assert end > start

    def test_default_duration_prefers_media_value(self) -> None:
        assert yodeck._default_duration({'default_duration': 42}) == 42

    def test_default_duration_falls_back_to_settings(self) -> None:
        expected = int(settings['default_duration'])
        assert yodeck._default_duration({'default_duration': 0}) == expected

    def test_webpage_url_read_from_arguments(self) -> None:
        detail = {'arguments': {'url': 'https://example.com/page'}}
        assert yodeck._webpage_url(detail) == 'https://example.com/page'

    def test_webpage_url_scans_argument_values(self) -> None:
        # Unknown key, but the value is a URL — the value scan finds it.
        detail = {'arguments': {'target_location': 'https://example.com/x'}}
        assert yodeck._webpage_url(detail) == 'https://example.com/x'

    def test_resolve_file_download_from_url(self) -> None:
        detail = {
            'file_extension': 'mp4',
            'arguments': {'download_from_url': 'https://cdn/x.mp4'},
        }
        assert yodeck._resolve_file(detail, 'video') == (
            'https://cdn/x.mp4',
            '.mp4',
        )

    def test_resolve_file_play_from_url_for_video(self) -> None:
        # Uploaded videos expose the transcoded MP4 at play_from_url.
        detail = {
            'file_extension': 'mp4',
            'arguments': {
                'download_from_url': None,
                'play_from_url': 'https://cdn/1080p.mp4',
            },
        }
        assert yodeck._resolve_file(detail, 'video') == (
            'https://cdn/1080p.mp4',
            '.mp4',
        )

    def test_resolve_file_image_thumbnail_fallback(self) -> None:
        # An uploaded image with no source URL falls back to the resized
        # render; its extension comes from the URL (jpg), not the stored one.
        detail = {
            'file_extension': 'png',
            'arguments': {'download_from_url': None},
            'thumbnail_url': 'https://cdn/a/b/1/resized.jpg',
        }
        assert yodeck._resolve_file(detail, 'image') == (
            'https://cdn/a/b/1/resized.jpg',
            '.jpg',
        )

    def test_resolve_file_none_for_video_without_url(self) -> None:
        # No file URL and no thumbnail fallback for video → skip.
        assert (
            yodeck._resolve_file(
                {'arguments': {}, 'thumbnail_url': 'https://cdn/x/poster.jpg'},
                'video',
            )
            is None
        )

    def test_file_ext_prefers_field(self) -> None:
        assert (
            yodeck._file_ext({'file_extension': 'mp4'}, 'https://x/y')
            == '.mp4'
        )

    def test_file_ext_from_url_when_field_missing(self) -> None:
        assert yodeck._file_ext({}, 'https://x/y.png?v=1') == '.png'

    def test_file_ext_sanitises_path_traversal(self) -> None:
        # A hostile file_extension must not escape the asset directory.
        assert (
            yodeck._file_ext(
                {'file_extension': '/../../etc/passwd'}, 'https://x/y'
            )
            == '.etcpasswd'
        )

    def test_first_http_url_rejects_stream_scheme(self) -> None:
        # validate_url accepts rtsp://, but this helper feeds webpage URIs
        # and requests.get downloads — http(s) only.
        assert ingest.first_http_url(['rtsp://cam/stream']) is None
        assert (
            ingest.first_http_url(['rtsp://cam/stream', 'https://ok/x'])
            == 'https://ok/x'
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_get_provider_known(self) -> None:
        assert isinstance(get_provider('yodeck'), yodeck.YodeckProvider)

    def test_get_provider_unknown(self) -> None:
        assert get_provider('nope') is None

    def test_list_provider_meta_includes_yodeck(self) -> None:
        keys = {meta['key'] for meta in list_provider_meta()}
        assert 'yodeck' in keys


# ---------------------------------------------------------------------------
# import_item (DB-backed)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestImportItem:
    def _detail_response(self, detail: dict[str, Any]) -> MagicMock:
        return _fake_response(200, detail)

    def test_idempotent_reimport_skips(self) -> None:
        asset = Asset.objects.create(
            asset_id='already',
            name='Existing',
            uri='https://example.com/',
            mimetype='webpage',
            duration=10,
            metadata={
                'import_source': {'provider': 'yodeck', 'remote_id': '77'}
            },
        )
        with patch(
            'anthias_server.lib.integrations.yodeck._session.get'
        ) as get_mock:
            outcome = PROVIDER.import_item('tok', '77')
        # Short-circuits before any HTTP call.
        get_mock.assert_not_called()
        assert outcome.skipped is True
        assert outcome.success is True
        assert outcome.asset_id == asset.asset_id

    @patch('anthias_server.lib.integrations.yodeck._session.get')
    def test_unsupported_type_skipped(self, get_mock: MagicMock) -> None:
        get_mock.return_value = self._detail_response(
            {'id': 5, 'name': 'Song', 'media_origin': {'type': 'audio'}}
        )
        outcome = PROVIDER.import_item('tok', '5')
        assert outcome.skipped is True
        assert outcome.success is False
        assert 'supported' in (outcome.reason or '').lower()

    @patch('anthias_server.lib.integrations.yodeck._session.get')
    def test_webpage_without_url_skipped(self, get_mock: MagicMock) -> None:
        get_mock.return_value = self._detail_response(
            {
                'id': 3,
                'name': 'Site',
                'media_origin': {'type': 'webpage'},
                'arguments': {},
            }
        )
        outcome = PROVIDER.import_item('tok', '3')
        assert outcome.skipped is True

    @patch('anthias_server.lib.integrations.yodeck._session.get')
    def test_internal_yodeck_app_webpage_skipped(
        self, get_mock: MagicMock
    ) -> None:
        # A webpage whose URL is Yodeck-hosted is an internal app/widget
        # and must not be imported as a (broken) webpage asset.
        get_mock.return_value = self._detail_response(
            {
                'id': 3,
                'name': 'Weather widget',
                'media_origin': {'type': 'webpage'},
                'arguments': {'url': 'https://app.yodeck.com/widgets/weather'},
            }
        )
        outcome = PROVIDER.import_item('tok', '3')
        assert outcome.skipped is True
        assert 'app' in (outcome.reason or '').lower()

    @patch('anthias_server.lib.integrations.yodeck._session.get')
    def test_image_without_file_url_skipped(self, get_mock: MagicMock) -> None:
        get_mock.return_value = self._detail_response(
            {
                'id': 1,
                'name': 'Pic',
                'media_origin': {'type': 'image'},
                'arguments': {},
            }
        )
        outcome = PROVIDER.import_item('tok', '1')
        assert outcome.skipped is True
        assert 're-upload' in (outcome.reason or '')

    @patch(
        'anthias_server.api.serializers.mixins.url_fails', return_value=False
    )
    @patch('anthias_server.lib.integrations.yodeck._session.get')
    def test_webpage_import_creates_asset(
        self, get_mock: MagicMock, _url_fails: MagicMock
    ) -> None:
        get_mock.return_value = self._detail_response(
            {
                'id': 3,
                'name': 'Wireload',
                'media_origin': {'type': 'webpage'},
                'default_duration': 15,
                'arguments': {'url': 'https://wireload.net/'},
                'availability_schedule': {
                    'enable': True,
                    'available_after': '2024-05-20T12:38:00Z',
                    'available_before': '2024-06-21T12:38:00Z',
                },
            }
        )
        outcome = PROVIDER.import_item('tok', '3', enable=True)
        assert outcome.success is True and not outcome.skipped

        asset = Asset.objects.get(asset_id=outcome.asset_id)
        assert asset.mimetype == 'webpage'
        assert asset.uri == 'https://wireload.net/'
        assert asset.is_enabled is True
        assert asset.duration == 15
        assert asset.metadata['import_source'] == {
            'provider': 'yodeck',
            'remote_id': '3',
        }
        assert asset.start_date == datetime(
            2024, 5, 20, 12, 38, tzinfo=timezone.utc
        )
        assert asset.end_date == datetime(
            2024, 6, 21, 12, 38, tzinfo=timezone.utc
        )

    @patch('anthias_server.lib.integrations.yodeck._session.get')
    def test_image_import_downloads_and_creates_asset(
        self,
        get_mock: MagicMock,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(settings, 'assetdir', str(tmp_path))
        detail = {
            'id': 1,
            'name': 'Pic',
            'media_origin': {'type': 'image'},
            'file_extension': 'png',
            'default_duration': 20,
            'arguments': {'download_from_url': 'https://cdn/x.png'},
            'availability_schedule': {},
        }

        def _get(url: str, **kwargs: Any) -> MagicMock:
            if kwargs.get('stream'):
                return _fake_stream_response(200, [b'\x89PNG\r\n\x1a\n'])
            return self._detail_response(detail)

        get_mock.side_effect = _get

        outcome = PROVIDER.import_item('tok', '1', enable=False)
        assert outcome.success is True and not outcome.skipped

        asset = Asset.objects.get(asset_id=outcome.asset_id)
        assert asset.mimetype == 'image'
        assert asset.uri == str(tmp_path / f'{asset.asset_id}.png')
        assert (tmp_path / f'{asset.asset_id}.png').is_file()
        assert asset.is_enabled is False
        # No staged temp files left behind.
        assert not list(tmp_path.glob('.import-*'))

    @patch('anthias_server.lib.integrations.yodeck._session.get')
    def test_image_download_failure_cleans_up(
        self,
        get_mock: MagicMock,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(settings, 'assetdir', str(tmp_path))
        detail = {
            'id': 1,
            'name': 'Pic',
            'media_origin': {'type': 'image'},
            'file_extension': 'png',
            'arguments': {'download_from_url': 'https://cdn/x.png'},
        }

        def _get(url: str, **kwargs: Any) -> MagicMock:
            if kwargs.get('stream'):
                return _fake_stream_response(404, [])
            return self._detail_response(detail)

        get_mock.side_effect = _get

        with pytest.raises(ProviderImportError):
            PROVIDER.import_item('tok', '1')

        # Neither the .part nor the staged file survive a failed download.
        assert not list(tmp_path.glob('.import-*'))
        assert Asset.objects.count() == 0

    def _capture_download_headers(
        self,
        get_mock: MagicMock,
        detail: dict[str, Any],
    ) -> dict[str, str]:
        captured: dict[str, str] = {}

        def _get(url: str, **kwargs: Any) -> MagicMock:
            if kwargs.get('stream'):
                captured.update(kwargs.get('headers') or {})
                return _fake_stream_response(200, [b'\x89PNG\r\n'])
            return self._detail_response(detail)

        get_mock.side_effect = _get
        return captured

    @patch('anthias_server.lib.integrations.yodeck._session.get')
    def test_download_omits_token_for_foreign_host(
        self,
        get_mock: MagicMock,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A pre-signed CDN original on a non-Yodeck host must NOT receive
        # the Yodeck API token.
        monkeypatch.setitem(settings, 'assetdir', str(tmp_path))
        detail = {
            'id': 1,
            'name': 'Pic',
            'media_origin': {'type': 'image'},
            'file_extension': 'png',
            'arguments': {
                'download_from_url': 'https://cdn.example.com/x.png'
            },
        }
        captured = self._capture_download_headers(get_mock, detail)
        PROVIDER.import_item('tok', '1')
        assert 'Authorization' not in captured

    @patch('anthias_server.lib.integrations.yodeck._session.get')
    def test_download_sends_token_for_yodeck_host(
        self,
        get_mock: MagicMock,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(settings, 'assetdir', str(tmp_path))
        detail = {
            'id': 1,
            'name': 'Pic',
            'media_origin': {'type': 'image'},
            'file_extension': 'png',
            'arguments': {
                'download_from_url': 'https://app.yodeck.com/media/orig/x.png'
            },
        }
        captured = self._capture_download_headers(get_mock, detail)
        PROVIDER.import_item('tok', '1')
        assert captured.get('Authorization') == 'Token tok'


# ---------------------------------------------------------------------------
# HTTP endpoints (provider layer mocked)
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def validate_url() -> str:
    return reverse('api:import_validate_v2', kwargs={'provider': 'yodeck'})


@pytest.fixture
def item_url() -> str:
    return reverse('api:import_item_v2', kwargs={'provider': 'yodeck'})


@pytest.mark.django_db
class TestImportValidateEndpoint:
    @mock.patch('anthias_server.api.views.v2.get_provider')
    def test_valid_token_returns_items(
        self,
        provider_mock: MagicMock,
        api_client: APIClient,
        validate_url: str,
    ) -> None:
        provider = MagicMock()
        provider.validate_token.return_value = True
        provider.list_media.return_value = [
            RemoteMediaItem('1', 'Pic', 'image', True),
            RemoteMediaItem('4', 'Song', 'audio', False, 'unsupported'),
        ]
        provider_mock.return_value = provider

        response = api_client.post(
            validate_url, {'token': 'good'}, format='json'
        )
        assert response.status_code == 200
        body = response.json()
        assert body['valid'] is True
        assert len(body['items']) == 2
        assert body['items'][0]['media_type'] == 'image'
        # ``raw`` must not leak to the browser.
        assert 'raw' not in body['items'][0]

    @mock.patch('anthias_server.api.views.v2.get_provider')
    def test_invalid_token(
        self,
        provider_mock: MagicMock,
        api_client: APIClient,
        validate_url: str,
    ) -> None:
        provider = MagicMock()
        provider.validate_token.return_value = False
        provider_mock.return_value = provider
        response = api_client.post(
            validate_url, {'token': 'bad'}, format='json'
        )
        assert response.status_code == 200
        assert response.json() == {'valid': False}
        provider.list_media.assert_not_called()

    @mock.patch('anthias_server.api.views.v2.get_provider')
    def test_network_error_returns_502(
        self,
        provider_mock: MagicMock,
        api_client: APIClient,
        validate_url: str,
    ) -> None:
        provider = MagicMock()
        provider.validate_token.side_effect = requests.ConnectionError('x')
        provider_mock.return_value = provider
        response = api_client.post(validate_url, {'token': 'x'}, format='json')
        assert response.status_code == 502
        assert response.json()['valid'] is False

    @mock.patch('anthias_server.api.views.v2.get_provider', return_value=None)
    def test_unknown_provider_404(
        self,
        _provider_mock: MagicMock,
        api_client: APIClient,
    ) -> None:
        url = reverse('api:import_validate_v2', kwargs={'provider': 'nope'})
        response = api_client.post(url, {'token': 'x'}, format='json')
        assert response.status_code == 404

    def test_missing_token_400(
        self, api_client: APIClient, validate_url: str
    ) -> None:
        # get_provider('yodeck') resolves; serializer rejects empty body.
        response = api_client.post(validate_url, {}, format='json')
        assert response.status_code == 400


@pytest.mark.django_db
class TestImportItemEndpoint:
    @mock.patch('anthias_server.api.views.v2.get_provider')
    def test_success(
        self,
        provider_mock: MagicMock,
        api_client: APIClient,
        item_url: str,
    ) -> None:
        provider = MagicMock()
        provider.import_item.return_value = ImportOutcome(
            success=True, asset_id='new-asset'
        )
        provider_mock.return_value = provider

        response = api_client.post(
            item_url,
            {'token': 'tok', 'remote_id': '1', 'enable': True},
            format='json',
        )
        assert response.status_code == 200
        body = response.json()
        assert body['success'] is True
        assert body['asset_id'] == 'new-asset'
        _, kwargs = provider.import_item.call_args
        assert kwargs['enable'] is True

    @mock.patch('anthias_server.api.views.v2.get_provider')
    def test_provider_error_returns_per_item_error(
        self,
        provider_mock: MagicMock,
        api_client: APIClient,
        item_url: str,
    ) -> None:
        provider = MagicMock()
        provider.import_item.side_effect = ProviderImportError('nope')
        provider_mock.return_value = provider

        response = api_client.post(
            item_url,
            {'token': 'tok', 'remote_id': '1'},
            format='json',
        )
        assert response.status_code == 200
        body = response.json()
        assert body['success'] is False
        assert body['error'] == 'nope'

    @mock.patch('anthias_server.api.views.v2.get_provider')
    def test_network_error_returns_502(
        self,
        provider_mock: MagicMock,
        api_client: APIClient,
        item_url: str,
    ) -> None:
        provider = MagicMock()
        provider.import_item.side_effect = requests.ConnectionError('x')
        provider_mock.return_value = provider

        response = api_client.post(
            item_url,
            {'token': 'tok', 'remote_id': '1'},
            format='json',
        )
        assert response.status_code == 502
        assert response.json()['success'] is False


# ---------------------------------------------------------------------------
# Wizard page + settings entry (template rendering)
# ---------------------------------------------------------------------------


_COMMAND_PROVIDER = (
    'anthias_server.app.management.commands.import_content.get_provider'
)


class TestImportContentCommand:
    def _provider(self, items: list[RemoteMediaItem]) -> MagicMock:
        provider = MagicMock()
        provider.label = 'Yodeck'
        provider.validate_token.return_value = True
        provider.list_media.return_value = items
        return provider

    @mock.patch(_COMMAND_PROVIDER)
    def test_dry_run_lists_without_importing(
        self, provider_mock: MagicMock
    ) -> None:
        provider = self._provider(
            [
                RemoteMediaItem('1', 'Pic', 'image', True),
                RemoteMediaItem('4', 'Song', 'audio', False, 'unsupported'),
            ]
        )
        provider_mock.return_value = provider
        out = StringIO()
        call_command(
            'import_content',
            '--provider',
            'yodeck',
            '--token',
            'x',
            '--dry-run',
            stdout=out,
        )
        provider.import_item.assert_not_called()
        assert '1 importable' in out.getvalue()

    @mock.patch(_COMMAND_PROVIDER)
    def test_import_runs_and_reports_counts(
        self, provider_mock: MagicMock
    ) -> None:
        provider = self._provider([RemoteMediaItem('1', 'Pic', 'image', True)])
        provider.import_item.return_value = ImportOutcome(
            success=True, asset_id='a'
        )
        provider_mock.return_value = provider
        out = StringIO()
        call_command(
            'import_content',
            '--provider',
            'yodeck',
            '--token',
            'x',
            stdout=out,
        )
        provider.import_item.assert_called_once()
        assert '1 imported' in out.getvalue()

    @mock.patch(_COMMAND_PROVIDER, return_value=None)
    def test_unknown_provider_errors(self, _provider_mock: MagicMock) -> None:
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command(
                'import_content', '--provider', 'nope', '--token', 'x'
            )


@pytest.mark.django_db
class TestImportWizardPage:
    def test_wizard_unknown_provider_404(self) -> None:
        # The view resolves the provider from the registry before
        # rendering, so an unknown key 404s without touching a template
        # (and without the redis-backed base layout the full-page render
        # would pull in — that path is exercised in the dev stack).
        url = reverse(
            'anthias_app:import_content', kwargs={'provider': 'nope'}
        )
        response = Client().get(url)
        assert response.status_code == 404
