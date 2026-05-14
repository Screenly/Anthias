"""Unit tests for the Screenly v4.1 migration helper + API endpoints.

These tests never reach the network. The module-level
``_session`` (an ``AnthiasSession``) is patched on the
``anthias_server.lib.screenly_migration`` module so we drive each
branch deterministically — token-validation outcomes, group
get-or-create idempotency, file-vs-URL asset routing, filename
reconstruction, error surfaces.

The HTTP-level tests use DRF's ``APIClient`` against the registered v2
routes so the serializer + view wiring is also under test, not just
the helper functions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.urls import reverse
from rest_framework.test import APIClient

from anthias_server.app.models import Asset
from anthias_server.lib import screenly_migration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_response(
    status_code: int,
    json_body: Any = None,
    text: str = '',
) -> MagicMock:
    """Build a stand-in for ``requests.Response`` with the surface our
    code touches: ``status_code``, ``ok``, ``json()``, ``text``,
    ``raise_for_status()``. Tests pass an explicit body so we can also
    cover the malformed-JSON branch by passing ``json_body=...`` with
    a ``ValueError`` side-effect via ``_json_raises_response``."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 400
    resp.text = text
    resp.json.return_value = json_body
    if not resp.ok:
        resp.raise_for_status.side_effect = requests.HTTPError(
            f'HTTP {status_code}', response=resp
        )
    return resp


def _json_raises_response(status_code: int, text: str = '') -> MagicMock:
    resp = _fake_response(status_code, text=text)
    resp.json.side_effect = ValueError('not json')
    return resp


# ---------------------------------------------------------------------------
# Pure-function tests (no network surface involved)
# ---------------------------------------------------------------------------


class TestBuildUploadFilename:
    """`_build_upload_filename` should reconstruct an
    operator-recognisable filename from ``Asset.name`` (which Anthias
    keeps) plus the on-disk extension (which survives upload). It must
    also strip path separators and control characters so a malicious
    or sloppy ``Asset.name`` can't poison the multipart filename."""

    def _asset(self, name: str) -> Asset:
        return Asset(name=name, uri='/data/anthias_assets/abc.png')

    def test_pretty_name_gets_disk_extension(self) -> None:
        result = screenly_migration._build_upload_filename(
            self._asset('My Day 2'), Path('/data/anthias_assets/abc.mp4')
        )
        assert result == 'My Day 2.mp4'

    def test_blank_name_falls_back_to_disk_filename(self) -> None:
        result = screenly_migration._build_upload_filename(
            self._asset(''), Path('/data/anthias_assets/abc.png')
        )
        assert result == 'abc.png'

    def test_whitespace_only_name_falls_back(self) -> None:
        result = screenly_migration._build_upload_filename(
            self._asset('   '), Path('/data/anthias_assets/abc.png')
        )
        assert result == 'abc.png'

    def test_name_already_has_extension_is_not_doubled(self) -> None:
        result = screenly_migration._build_upload_filename(
            self._asset('Photo.png'), Path('/data/anthias_assets/abc.png')
        )
        assert result == 'Photo.png'

    def test_path_traversal_chars_are_sanitised(self) -> None:
        result = screenly_migration._build_upload_filename(
            self._asset('../etc/passwd'),
            Path('/data/anthias_assets/abc.png'),
        )
        assert '/' not in result and '\\' not in result
        assert result.endswith('.png')

    def test_control_chars_replaced(self) -> None:
        # NUL + SOH must never reach the multipart filename — some
        # downstream stores reject them and others silently truncate.
        result = screenly_migration._build_upload_filename(
            self._asset('Photo\x00\x01drop'),
            Path('/data/anthias_assets/abc.png'),
        )
        assert '\x00' not in result and '\x01' not in result
        assert result.endswith('.png')

    def test_trailing_dots_are_stripped(self) -> None:
        # ``Photo...`` would otherwise become ``Photo....png`` — ugly
        # and at risk of being rejected by Screenly's filename validator.
        result = screenly_migration._build_upload_filename(
            self._asset('Photo...'),
            Path('/data/anthias_assets/abc.png'),
        )
        assert result == 'Photo.png'


class TestResolveLocalPath:
    """`_resolve_local_path` mirrors the realpath+startswith guard from
    ``views_files.anthias_assets`` so the upload path can't be tricked
    into reading files outside the asset directory."""

    def test_returns_none_for_http_url(self) -> None:
        assert (
            screenly_migration._resolve_local_path('https://example.com/x')
            is None
        )

    def test_returns_none_for_unrelated_path(self) -> None:
        assert screenly_migration._resolve_local_path('/etc/passwd') is None

    def test_returns_none_for_neighbour_directory(self) -> None:
        # Without the trailing-sep guard, ``startswith`` would accept
        # ``/data/anthias_assets_evil/...`` even though it's a different
        # directory entirely.
        assert (
            screenly_migration._resolve_local_path(
                '/data/anthias_assets_evil/x'
            )
            is None
        )


class TestAuthHeaders:
    def test_token_scheme_and_prefer_header(self) -> None:
        # The live API rejects raw API keys and Bearer tokens — only
        # ``Token <key>`` works. The Prefer header is what makes
        # create endpoints return the new row instead of 204.
        assert screenly_migration._auth_headers('abc.123') == {
            'Authorization': 'Token abc.123',
            'Prefer': 'return=representation',
        }

    def test_session_carries_anthias_user_agent(self) -> None:
        # Screenly ops should be able to spot migration traffic in
        # their access logs without parsing the path — otherwise it
        # looks identical to any other ``python-requests/*`` client.
        ua = screenly_migration._session.headers['User-Agent']
        assert isinstance(ua, str)
        assert ua.startswith('Anthias/')
        assert '(+https://anthias.screenly.io)' in ua


# ---------------------------------------------------------------------------
# validate_token
# ---------------------------------------------------------------------------


class TestValidateToken:
    @patch('anthias_server.lib.screenly_migration._session.get')
    def test_returns_true_on_200(self, get_mock: MagicMock) -> None:
        get_mock.return_value = _fake_response(200, json_body=[])
        assert screenly_migration.validate_token('good') is True
        # Sanity-check the URL + auth surface
        _, kwargs = get_mock.call_args
        assert kwargs['headers']['Authorization'] == 'Token good'

    @patch('anthias_server.lib.screenly_migration._session.get')
    @pytest.mark.parametrize('code', [401, 403])
    def test_returns_false_on_auth_failure(
        self, get_mock: MagicMock, code: int
    ) -> None:
        get_mock.return_value = _fake_response(code, json_body={'error': 'no'})
        assert screenly_migration.validate_token('bad') is False

    @patch('anthias_server.lib.screenly_migration._session.get')
    def test_raises_on_5xx(self, get_mock: MagicMock) -> None:
        # 5xx should propagate as a RequestException so the caller can
        # distinguish "your token is bad" from "Screenly is down".
        get_mock.return_value = _fake_response(503)
        with pytest.raises(requests.HTTPError):
            screenly_migration.validate_token('x')

    @patch('anthias_server.lib.screenly_migration._session.get')
    def test_propagates_network_error(self, get_mock: MagicMock) -> None:
        get_mock.side_effect = requests.ConnectionError('boom')
        with pytest.raises(requests.ConnectionError):
            screenly_migration.validate_token('x')


# ---------------------------------------------------------------------------
# ensure_asset_group
# ---------------------------------------------------------------------------


class TestEnsureAssetGroup:
    @patch('anthias_server.lib.screenly_migration._session.post')
    @patch('anthias_server.lib.screenly_migration._session.get')
    def test_returns_existing_id_without_post(
        self, get_mock: MagicMock, post_mock: MagicMock
    ) -> None:
        # Idempotency: a re-run shouldn't create "Migrated from
        # Anthias (2)" on every pass.
        get_mock.return_value = _fake_response(
            200, json_body=[{'id': 'GROUP123', 'title': 'g'}]
        )
        gid = screenly_migration.ensure_asset_group('t', 'g')
        assert gid == 'GROUP123'
        post_mock.assert_not_called()

    @patch('anthias_server.lib.screenly_migration._session.post')
    @patch('anthias_server.lib.screenly_migration._session.get')
    def test_creates_group_when_lookup_empty(
        self, get_mock: MagicMock, post_mock: MagicMock
    ) -> None:
        get_mock.return_value = _fake_response(200, json_body=[])
        post_mock.return_value = _fake_response(
            201, json_body=[{'id': 'NEW999', 'title': 'g'}]
        )
        gid = screenly_migration.ensure_asset_group('t', 'g')
        assert gid == 'NEW999'
        _, kwargs = post_mock.call_args
        assert kwargs['json'] == {'title': 'g'}

    @patch('anthias_server.lib.screenly_migration._session.post')
    @patch('anthias_server.lib.screenly_migration._session.get')
    def test_falls_back_to_create_when_lookup_fails(
        self, get_mock: MagicMock, post_mock: MagicMock
    ) -> None:
        # A 5xx on the lookup shouldn't block the migration — create
        # would create a duplicate on rare re-tries but is the right
        # safer default for the wizard's "click Continue → progress".
        get_mock.return_value = _fake_response(503)
        post_mock.return_value = _fake_response(
            201, json_body=[{'id': 'NEW999'}]
        )
        gid = screenly_migration.ensure_asset_group('t', 'g')
        assert gid == 'NEW999'

    @patch('anthias_server.lib.screenly_migration._session.post')
    @patch('anthias_server.lib.screenly_migration._session.get')
    def test_raises_screenly_error_on_create_failure(
        self, get_mock: MagicMock, post_mock: MagicMock
    ) -> None:
        get_mock.return_value = _fake_response(200, json_body=[])
        post_mock.return_value = _fake_response(
            400, json_body={'error': 'bad'}
        )
        with pytest.raises(screenly_migration.ScreenlyMigrationError) as exc:
            screenly_migration.ensure_asset_group('t', 'g')
        assert '400' in str(exc.value)


# ---------------------------------------------------------------------------
# migrate_asset
# ---------------------------------------------------------------------------


class TestMigrateAsset:
    def _url_asset(self) -> Asset:
        return Asset(
            asset_id='url-asset',
            name='Wireload',
            uri='https://wireload.net/',
        )

    def _local_asset(self, tmp_path: Path) -> Asset:
        target = tmp_path / 'abc123.png'
        target.write_bytes(b'\x89PNG\r\n')
        return Asset(
            asset_id='local-asset',
            name='My Day 2',
            uri=str(target),
        )

    @patch('anthias_server.lib.screenly_migration._session.post')
    def test_url_asset_uses_source_url(self, post_mock: MagicMock) -> None:
        post_mock.return_value = _fake_response(
            201, json_body=[{'id': 'OUTID'}]
        )
        result = screenly_migration.migrate_asset(
            'tok', self._url_asset(), asset_group_id='GID'
        )
        _, kwargs = post_mock.call_args
        # URL-backed assets must NOT send a ``files`` arg; multipart
        # would trigger Screenly's "missing source_url" validator.
        assert kwargs['files'] is None
        assert kwargs['data']['source_url'] == 'https://wireload.net/'
        assert kwargs['data']['asset_group_id'] == 'GID'
        assert kwargs['data']['title'] == 'Wireload'
        # migrate_asset returns dict[str, Any] | list[Any]; the
        # postgREST-style response shape is a single-row list, so
        # narrow before indexing or mypy complains.
        assert isinstance(result, list)
        assert result[0]['id'] == 'OUTID'

    @patch('anthias_server.lib.screenly_migration._session.post')
    def test_local_asset_uses_multipart_with_pretty_filename(
        self,
        post_mock: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Point ANTHIAS_ASSETS_ROOT at the temp dir so the safety guard
        # in ``_resolve_local_path`` lets the test file through.
        monkeypatch.setattr(
            screenly_migration, 'ANTHIAS_ASSETS_ROOT', tmp_path
        )
        asset = self._local_asset(tmp_path)
        post_mock.return_value = _fake_response(
            201, json_body=[{'id': 'OUTID'}]
        )

        screenly_migration.migrate_asset('tok', asset, asset_group_id='GID')

        _, kwargs = post_mock.call_args
        assert 'source_url' not in kwargs['data']
        assert kwargs['data']['title'] == 'My Day 2'
        assert kwargs['data']['asset_group_id'] == 'GID'
        # The multipart filename is what Screenly's UI shows the
        # operator — preserve "My Day 2.png", not the on-disk UUID.
        filename, _ = kwargs['files']['file']
        assert filename == 'My Day 2.png'

    @patch('anthias_server.lib.screenly_migration._session.post')
    def test_local_asset_missing_file_raises_error(
        self,
        post_mock: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            screenly_migration, 'ANTHIAS_ASSETS_ROOT', tmp_path
        )
        asset = Asset(
            asset_id='gone',
            name='gone',
            uri=str(tmp_path / 'never-existed.mp4'),
        )
        with pytest.raises(screenly_migration.ScreenlyMigrationError) as exc:
            screenly_migration.migrate_asset('tok', asset)
        assert 'File not found' in str(exc.value)
        post_mock.assert_not_called()

    @patch('anthias_server.lib.screenly_migration._session.post')
    def test_local_asset_open_oserror_surfaces_as_per_asset_error(
        self,
        post_mock: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # File passes is_file() but open() raises (PermissionError /
        # transient IO). Must surface as ScreenlyMigrationError —
        # otherwise it bubbles as a 500 from the API view and the
        # operator gets a generic failure instead of a per-asset row.
        monkeypatch.setattr(
            screenly_migration, 'ANTHIAS_ASSETS_ROOT', tmp_path
        )
        target = tmp_path / 'locked.png'
        target.write_bytes(b'\x89PNG\r\n')
        asset = Asset(asset_id='locked', name='Locked', uri=str(target))

        with patch(
            'pathlib.Path.open',
            side_effect=PermissionError(13, 'Permission denied'),
        ):
            with pytest.raises(
                screenly_migration.ScreenlyMigrationError
            ) as exc:
                screenly_migration.migrate_asset('tok', asset)

        assert 'Could not read locked.png' in str(exc.value)
        assert 'Permission denied' in str(exc.value)
        post_mock.assert_not_called()

    @patch('anthias_server.lib.screenly_migration._session.post')
    def test_screenly_rejection_surfaces_status_and_detail(
        self, post_mock: MagicMock
    ) -> None:
        post_mock.return_value = _fake_response(
            413,
            json_body={'error': 'payload too large'},
        )
        with pytest.raises(screenly_migration.ScreenlyMigrationError) as exc:
            screenly_migration.migrate_asset('tok', self._url_asset())
        msg = str(exc.value)
        assert '413' in msg
        assert 'payload too large' in msg

    @patch('anthias_server.lib.screenly_migration._session.post')
    def test_no_uri_asset_raises_before_request(
        self, post_mock: MagicMock
    ) -> None:
        asset = Asset(asset_id='blank', name='blank', uri='')
        with pytest.raises(screenly_migration.ScreenlyMigrationError):
            screenly_migration.migrate_asset('tok', asset)
        post_mock.assert_not_called()

    @patch('anthias_server.lib.screenly_migration._session.post')
    def test_html_error_body_collapses_to_one_line(
        self, post_mock: MagicMock
    ) -> None:
        # Defends the UI against multi-line stack-trace bodies leaking
        # into the per-asset error column.
        post_mock.return_value = _json_raises_response(
            500, text='<html>\n<body>Oops</body>\n</html>'
        )
        with pytest.raises(screenly_migration.ScreenlyMigrationError) as exc:
            screenly_migration.migrate_asset('tok', self._url_asset())
        assert '\n' not in str(exc.value)


# ---------------------------------------------------------------------------
# HTTP-level tests (DRF API client)
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def validate_url() -> str:
    return reverse('api:screenly_validate_token_v2')


@pytest.fixture
def migrate_url() -> str:
    return reverse('api:screenly_migrate_asset_v2')


@pytest.mark.django_db
class TestValidateTokenEndpoint:
    """The validate endpoint also primes the destination asset-group
    so the wizard's Continue button transitions straight to the asset
    picker on success. Tests live together because a half-mock (only
    validate, no group) would never match a real call sequence."""

    @mock.patch('anthias_server.api.views.v2.ensure_asset_group')
    @mock.patch('anthias_server.api.views.v2.validate_token')
    def test_valid_token_returns_group_id(
        self,
        validate_mock: MagicMock,
        group_mock: MagicMock,
        api_client: APIClient,
        validate_url: str,
    ) -> None:
        validate_mock.return_value = True
        group_mock.return_value = 'GROUPID'
        response = api_client.post(
            validate_url, {'token': 'good'}, format='json'
        )
        assert response.status_code == 200
        body = response.json()
        assert body['valid'] is True
        assert body['asset_group_id'] == 'GROUPID'
        assert body['asset_group_title'] == (
            screenly_migration.MIGRATION_ASSET_GROUP_TITLE
        )
        group_mock.assert_called_once_with(
            'good', screenly_migration.MIGRATION_ASSET_GROUP_TITLE
        )

    @mock.patch('anthias_server.api.views.v2.ensure_asset_group')
    @mock.patch('anthias_server.api.views.v2.validate_token')
    def test_invalid_token_skips_group_creation(
        self,
        validate_mock: MagicMock,
        group_mock: MagicMock,
        api_client: APIClient,
        validate_url: str,
    ) -> None:
        validate_mock.return_value = False
        response = api_client.post(
            validate_url, {'token': 'bad'}, format='json'
        )
        assert response.status_code == 200
        assert response.json() == {'valid': False}
        # Don't create a group for a bad token — that would litter
        # the operator's eventual real account if the same token
        # space is shared.
        group_mock.assert_not_called()

    @mock.patch('anthias_server.api.views.v2.validate_token')
    def test_network_error_returns_502(
        self,
        validate_mock: MagicMock,
        api_client: APIClient,
        validate_url: str,
    ) -> None:
        validate_mock.side_effect = requests.ConnectionError('boom')
        response = api_client.post(validate_url, {'token': 'x'}, format='json')
        assert response.status_code == 502
        assert response.json()['valid'] is False
        assert 'Could not reach Screenly' in response.json()['error']

    @mock.patch('anthias_server.api.views.v2.ensure_asset_group')
    @mock.patch('anthias_server.api.views.v2.validate_token')
    def test_group_create_failure_surfaces_as_502(
        self,
        validate_mock: MagicMock,
        group_mock: MagicMock,
        api_client: APIClient,
        validate_url: str,
    ) -> None:
        # Token validated but group creation failed — operator gets
        # one clear error, not N noisy ones. ``valid`` must come back
        # False on the 502 path: the field means "token validated AND
        # the wizard can proceed", so a partial-failure (token ok but
        # group setup busted) shouldn't read as ``valid: True`` to a
        # client that only checks that flag.
        validate_mock.return_value = True
        group_mock.side_effect = screenly_migration.ScreenlyMigrationError(
            'boom'
        )
        response = api_client.post(
            validate_url, {'token': 'good'}, format='json'
        )
        assert response.status_code == 502
        body = response.json()
        assert body['valid'] is False
        assert 'boom' in body['error']

    def test_missing_token_is_400(
        self, api_client: APIClient, validate_url: str
    ) -> None:
        response = api_client.post(validate_url, {}, format='json')
        assert response.status_code == 400


@pytest.mark.django_db
class TestMigrateAssetEndpoint:
    @pytest.fixture
    def asset(self) -> Asset:
        return Asset.objects.create(
            asset_id='test-asset',
            name='Test Asset',
            uri='https://example.com/',
            mimetype='webpage',
            duration=10,
            is_enabled=True,
        )

    @mock.patch('anthias_server.api.views.v2.migrate_asset')
    def test_success_returns_screenly_id(
        self,
        migrate_mock: MagicMock,
        api_client: APIClient,
        migrate_url: str,
        asset: Asset,
    ) -> None:
        migrate_mock.return_value = [{'id': '01SCREENLYID'}]
        response = api_client.post(
            migrate_url,
            {
                'token': 'tok',
                'asset_id': asset.asset_id,
                'asset_group_id': 'GID',
            },
            format='json',
        )
        assert response.status_code == 200
        body = response.json()
        assert body['success'] is True
        assert body['screenly_asset_id'] == '01SCREENLYID'
        # Group id must flow through — otherwise migrated assets
        # would scatter across the account instead of landing in
        # "Migrated from Anthias".
        _, kwargs = migrate_mock.call_args
        assert kwargs['asset_group_id'] == 'GID'

    @mock.patch('anthias_server.api.views.v2.migrate_asset')
    def test_screenly_rejection_returns_per_asset_error(
        self,
        migrate_mock: MagicMock,
        api_client: APIClient,
        migrate_url: str,
        asset: Asset,
    ) -> None:
        # Per-asset errors return 200 so the UI keeps progressing
        # through the queue; the body carries success=False.
        migrate_mock.side_effect = screenly_migration.ScreenlyMigrationError(
            'rejected'
        )
        response = api_client.post(
            migrate_url,
            {'token': 'tok', 'asset_id': asset.asset_id},
            format='json',
        )
        assert response.status_code == 200
        body = response.json()
        assert body['success'] is False
        assert body['error'] == 'rejected'

    @mock.patch('anthias_server.api.views.v2.migrate_asset')
    def test_network_error_returns_502(
        self,
        migrate_mock: MagicMock,
        api_client: APIClient,
        migrate_url: str,
        asset: Asset,
    ) -> None:
        migrate_mock.side_effect = requests.ConnectionError('boom')
        response = api_client.post(
            migrate_url,
            {'token': 'tok', 'asset_id': asset.asset_id},
            format='json',
        )
        assert response.status_code == 502
        assert response.json()['success'] is False

    def test_unknown_asset_id_returns_404(
        self, api_client: APIClient, migrate_url: str
    ) -> None:
        response = api_client.post(
            migrate_url,
            {'token': 'tok', 'asset_id': 'nope'},
            format='json',
        )
        assert response.status_code == 404
        assert response.json()['success'] is False
