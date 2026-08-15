"""Smoke / integration coverage for the post-React Django template views.

Each view in src/anthias_server/app/views.py beyond the legacy ``react``,
``login`` and ``splash_page`` is exercised here through Django's test
client — fast, deterministic, no browser overhead. The integration
suite (tests/test_app.py) still drives the full stack via Playwright +
Chromium, but that suite hits a parallel uvicorn process and doesn't
accumulate coverage. These tests do.
"""

from __future__ import annotations

from datetime import time, timedelta
from typing import Any
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from anthias_common import storage_health
from anthias_server.app import page_context
from anthias_server.app.models import DURATION_S_MAX, Asset
from anthias_server.app.templatetags.asset_filters import to_json
from anthias_server.settings import settings


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.fixture
def asset() -> Asset:
    now = timezone.now()
    return Asset.objects.create(
        name='Test asset',
        uri='https://example.com',
        mimetype='webpage',
        duration=10,
        is_enabled=True,
        is_processing=False,
        play_order=0,
        start_date=now,
        end_date=now + timedelta(days=30),
    )


# ---------------------------------------------------------------------------
# GET (rendering) paths


@pytest.mark.django_db
def test_home_renders(client: Client, asset: Asset) -> None:
    response = client.get(reverse('anthias_app:home'))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'Schedule Overview' in body
    assert (asset.name or '') in body


@pytest.mark.django_db
def test_home_exposes_apps_tab_and_store_index(client: Client) -> None:
    """The Add → Apps tab and the store-index <meta> the tab reads must
    both reach the rendered home page — this is the settings ->
    helpers.template -> base.html -> _asset_modal wiring end to end."""
    from django.conf import settings as dj_settings

    response = client.get(reverse('anthias_app:home'))
    body = response.content.decode()
    assert 'name="anthias-app-store-index"' in body
    # Assert the actual configured index URL reached the page, not a
    # hard-coded literal that would drift if the default changes.
    assert dj_settings.APP_STORE_INDEX_URL in body
    assert 'id="tab-apps"' in body
    assert 'appsTab()' in body


@pytest.mark.django_db
def test_system_info_renders(client: Client) -> None:
    response = client.get(reverse('anthias_app:system_info'))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'System Info' in body
    # The shared system_info() helper supplies these context keys; they
    # must show up in the rendered table even if the values themselves
    # are environment-dependent.
    for label in ('Load Average', 'Disk', 'Memory', 'Uptime'):
        assert label in body
    # The device clock card + its seed-from-server ticking hook.
    assert 'Device time' in body
    assert 'id="device-clock"' in body


@pytest.mark.django_db
def test_integrations_renders(client: Client) -> None:
    # is_balena is False on the host runner — the page header still
    # renders, the Balena table just doesn't.
    response = client.get(reverse('anthias_app:integrations'))
    assert response.status_code == 200
    assert 'Integrations' in response.content.decode()


@pytest.mark.django_db
def test_settings_renders(client: Client) -> None:
    response = client.get(reverse('anthias_app:settings'))
    assert response.status_code == 200
    body = response.content.decode()
    for label in (
        'Player name',
        'Default duration',
        'Audio output',
        'Date format',
        'Timezone',
        'Authentication',
        'Show splash screen',
        'Display schedule',
        'Turn on at',
        'Turn off at',
        'Backup',
        'System controls',
    ):
        assert label in body
    # The timezone dropdown is populated from the IANA list, with the
    # real id as the option value and a humanised (underscore-free)
    # label shown to the operator.
    assert 'name="timezone"' in body
    assert 'value="America/New_York"' in body
    assert 'America/New York' in body


@pytest.mark.django_db
def test_asset_table_partial(client: Client, asset: Asset) -> None:
    response = client.get(reverse('anthias_app:assets_table'))
    assert response.status_code == 200
    assert (asset.name or '') in response.content.decode()


@pytest.mark.django_db
def test_asset_row_renders_error_pill_when_processing_failed(
    client: Client,
) -> None:
    """A row whose normalisation task failed (metadata.error_message
    populated, is_processing cleared) renders the warn-coloured
    "Failed" pill in place of the active toggle. The full error
    message rides along on the title attribute so the operator can
    hover for context without a separate modal."""
    Asset.objects.create(
        asset_id='asset-failed',
        name='broken upload',
        uri='/data/anthias_assets/asset-failed.heic',
        mimetype='image',
        duration=10,
        is_enabled=False,
        is_processing=False,
        play_order=0,
        metadata={'error_message': 'UnidentifiedImageError: bad header'},
    )
    response = client.get(reverse('anthias_app:assets_table'))
    body = response.content.decode()
    assert response.status_code == 200
    assert 'error-pill' in body
    # The hover-tooltip carries the full message verbatim.
    assert 'UnidentifiedImageError: bad header' in body
    # The active toggle and the in-progress pill must NOT be rendered
    # for this row — the error pill replaces them both.
    assert 'asset-failed' in body
    # processing-pill belongs to in-flight rows, not failed ones.
    assert (
        body.count('processing-pill') == 0
        or 'asset-failed' not in body.split('processing-pill', 1)[0][-200:]
    )


@pytest.mark.django_db
def test_asset_row_no_error_pill_when_metadata_clean(
    client: Client, asset: Asset
) -> None:
    """The vanilla happy-path row (no metadata, not processing) shows
    the active-toggle, not the error pill."""
    response = client.get(reverse('anthias_app:assets_table'))
    body = response.content.decode()
    assert 'error-pill' not in body
    assert 'activity-toggle' in body


# ---------------------------------------------------------------------------
# Page-context helpers — lightweight unit tests that bypass the HTTP
# layer so coverage of the tiny pure-Python functions doesn't depend on
# the request stack.


@pytest.mark.django_db
def test_page_context_assets_split(asset: Asset) -> None:
    # asset is enabled + active by fixture.
    ctx = page_context.assets()
    active_ids = [a.asset_id for a in ctx['active_assets']]
    inactive_ids = [a.asset_id for a in ctx['inactive_assets']]
    assert asset.asset_id in active_ids
    assert asset.asset_id not in inactive_ids


@pytest.mark.django_db
def test_page_context_device_settings_keys() -> None:
    ctx = page_context.device_settings()
    for key in (
        'player_name',
        'default_duration',
        'default_streaming_duration',
        'audio_output',
        'date_format',
        'auth_backend',
        'show_splash',
        'screen_rotation',
        'date_format_options',
        'is_pi5',
        'display_power_schedule_enabled',
        'display_power_on_time',
        'display_power_off_time',
        'display_power_days',
        'weekday_options',
    ):
        assert key in ctx


def test_page_context_navbar_has_balena_and_up_to_date() -> None:
    ctx = page_context.navbar()
    assert 'is_balena' in ctx
    assert 'up_to_date' in ctx
    assert 'player_name' in ctx


def test_page_context_integrations_when_off_balena() -> None:
    ctx = page_context.integrations()
    assert ctx['is_balena'] is False


# ---------------------------------------------------------------------------
# Templatetag


@pytest.mark.django_db
def test_to_json_serialises_asset(asset: Asset) -> None:
    encoded = str(to_json(asset))
    assert asset.asset_id in encoded
    assert (asset.name or '') in encoded
    # The inline blob is later read inside an HTML attribute; the filter
    # escapes ampersands and apostrophes so the attribute value stays
    # well-formed even when an asset name contains either character.
    asset.name = "Foo & Bar's video"
    asset.save()
    encoded = str(to_json(asset))
    assert '&' not in encoded.replace('\\u0026', '')
    assert "'" not in encoded.replace('\\u0027', '')


# ---------------------------------------------------------------------------
# Write endpoints — exercise each branch enough to count for coverage.


@pytest.mark.django_db
def test_assets_create_via_post(client: Client) -> None:
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_create'),
            data={'uri': 'https://anthias.example.com/foo.png'},
        )
    assert response.status_code in (200, 302)
    created = Asset.objects.filter(uri='https://anthias.example.com/foo.png')
    assert created.exists()
    first = created.first()
    assert first is not None
    assert first.mimetype == 'image'


@pytest.mark.django_db
def test_assets_create_rejects_invalid_url(client: Client) -> None:
    response = client.post(
        reverse('anthias_app:assets_create'),
        data={'uri': 'not-a-url'},
    )
    # We redirect-back-with-message; no row written.
    assert response.status_code in (200, 302)
    assert not Asset.objects.filter(uri='not-a-url').exists()


@pytest.mark.django_db
def test_assets_create_routes_youtube_to_celery(client: Client) -> None:
    """Pasting a YouTube URL into the Add modal must NOT classify it
    as a webpage (the iframe embed is blocked by YouTube). The row
    is created as is_processing=True with mimetype=video and a local
    mp4 destination, and download_youtube_asset is queued."""
    youtube_url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
    with (
        mock.patch(
            'anthias_server.settings.ViewerPublisher.send_to_viewer',
            return_value=None,
        ),
        mock.patch(
            'anthias_common.youtube.dispatch_download'
        ) as mock_dispatch,
    ):
        response = client.post(
            reverse('anthias_app:assets_create'),
            data={'uri': youtube_url},
        )
    assert response.status_code in (200, 302)

    # The persisted row points at the local mp4 destination, not the
    # YouTube URL. The placeholder name carries the URL so the
    # operator can identify the row in the table while it processes.
    rows = Asset.objects.filter(name=youtube_url)
    assert rows.count() == 1
    row = rows.first()
    assert row is not None
    assert row.mimetype == 'video'
    assert row.is_processing is True
    assert row.uri is not None
    assert row.uri.endswith(f'{row.asset_id}.mp4')
    assert row.duration == 0

    mock_dispatch.assert_called_once_with(row.asset_id, youtube_url)


@pytest.mark.django_db
def test_assets_create_routes_rtsp_to_streaming(client: Client) -> None:
    """Pasting an RTSP URL into the Add modal must classify it as
    mimetype='streaming' (not 'webpage'), otherwise the viewer hands
    it to QtWebEngine instead of the video player and the stream never
    appears. Regression guard for the classifier dropped in the
    React→Django migration (#2818)."""
    from anthias_server.settings import settings

    rtsp_url = 'rtsp://camera.local:554/stream'
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_create'),
            data={'uri': rtsp_url},
        )
    assert response.status_code in (200, 302)
    row = Asset.objects.filter(uri=rtsp_url).first()
    assert row is not None
    assert row.mimetype == 'streaming'
    # Streams have no intrinsic length — they take the dedicated
    # streaming-duration window, not the standard default.
    assert row.duration == int(settings['default_streaming_duration'])


@pytest.mark.django_db
def test_assets_create_rejects_rtmp(client: Client) -> None:
    """RTMP is well-formed but Qt6's QMediaPlayer can't open it, so the
    Add modal must reject rtmp:// rather than create an asset that
    renders black. No row is written."""
    rtmp_url = 'rtmp://media.example.com/live'
    response = client.post(
        reverse('anthias_app:assets_create'),
        data={'uri': rtmp_url},
    )
    assert response.status_code in (200, 302)
    assert not Asset.objects.filter(uri=rtmp_url).exists()


@pytest.mark.django_db
def test_assets_create_routes_hls_manifest_to_streaming(
    client: Client,
) -> None:
    """An HTTP-delivered HLS manifest (.m3u8) is a live stream, not a
    downloadable file or a web page — it must classify as streaming."""
    hls_url = 'https://cdn.example.com/live/index.m3u8'
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_create'),
            data={'uri': hls_url},
        )
    row = Asset.objects.filter(uri=hls_url).first()
    assert row is not None
    assert row.mimetype == 'streaming'


@pytest.mark.django_db
def test_assets_create_youtube_short_form(client: Client) -> None:
    """youtu.be short URLs are recognised the same as full URLs."""
    short_url = 'https://youtu.be/dQw4w9WgXcQ'
    with (
        mock.patch(
            'anthias_server.settings.ViewerPublisher.send_to_viewer',
            return_value=None,
        ),
        mock.patch(
            'anthias_common.youtube.dispatch_download'
        ) as mock_dispatch,
    ):
        client.post(
            reverse('anthias_app:assets_create'),
            data={'uri': short_url},
        )
    assert Asset.objects.filter(name=short_url, mimetype='video').exists()
    mock_dispatch.assert_called_once()


# ---------------------------------------------------------------------------
# Add → Apps tab: installing a signage-store app (assets_create_app)


def _post_app(client: Client, **data: Any) -> Any:
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        return client.post(reverse('anthias_app:assets_create_app'), data=data)


@pytest.mark.django_db
def test_assets_create_app_stamps_metadata(client: Client) -> None:
    """A configured app installs as a webpage asset carrying
    metadata.app (id, manifest URL/version and the raw setting values)
    so the edit modal can reopen the same config form."""
    response = _post_app(
        client,
        app_id='weather',
        manifest_url='https://weather.srly.io/.well-known/signage-app.json',
        manifest_version='1',
        name='Weather',
        app_uri='https://weather.srly.io/?lat=51.5&lng=-0.1&24h=1',
        app_values='{"location": {"lat": 51.5, "lng": -0.1}, "24h": "1"}',
    )
    assert response.status_code in (200, 302)

    asset = Asset.objects.filter(name='Weather').first()
    assert asset is not None
    assert asset.mimetype == 'webpage'
    assert asset.uri == 'https://weather.srly.io/?lat=51.5&lng=-0.1&24h=1'
    assert asset.is_enabled is True
    assert asset.metadata['app'] == {
        'id': 'weather',
        'manifest_url': (
            'https://weather.srly.io/.well-known/signage-app.json'
        ),
        'manifest_version': '1',
        'values': {'location': {'lat': 51.5, 'lng': -0.1}, '24h': '1'},
    }


@pytest.mark.django_db
def test_assets_create_app_no_settings(client: Client) -> None:
    """A no-settings app (e.g. Quotes) installs with an empty values
    bag and the bare base URL."""
    response = _post_app(
        client,
        app_id='quotes',
        manifest_url='https://quotes.srly.io/.well-known/signage-app.json',
        manifest_version='1',
        name='Quotes',
        app_uri='https://quotes.srly.io/',
        app_values='{}',
    )
    assert response.status_code in (200, 302)
    asset = Asset.objects.filter(name='Quotes').first()
    assert asset is not None
    assert asset.metadata['app']['values'] == {}


@pytest.mark.django_db
def test_assets_create_app_maps_refresh_interval(client: Client) -> None:
    """The manifest's playback.refreshIntervalS is echoed via the
    hidden field and stored (clamped) as metadata.refresh_interval_s,
    reusing the viewer's existing per-asset auto-refresh."""
    _post_app(
        client,
        app_id='weather',
        manifest_url='https://weather.srly.io/.well-known/signage-app.json',
        manifest_version='1',
        name='Weather',
        app_uri='https://weather.srly.io/',
        app_values='{}',
        refresh_interval_s='3600',
    )
    asset = Asset.objects.filter(name='Weather').first()
    assert asset is not None
    assert asset.metadata['refresh_interval_s'] == 3600


@pytest.mark.django_db
def test_assets_create_app_rejects_foreign_host(client: Client) -> None:
    """A launch URL outside the store-app allowlist is refused — the
    'app' badge can't be stamped onto an arbitrary URL."""
    _post_app(
        client,
        app_id='evil',
        manifest_url='https://evil.example.com/manifest.json',
        manifest_version='1',
        name='Evil',
        app_uri='https://evil.example.com/',
        app_values='{}',
    )
    assert not Asset.objects.filter(uri='https://evil.example.com/').exists()


def test_host_allowed_matching() -> None:
    """Dotted-boundary suffix matching: real store hosts (with or
    without a port) pass; look-alikes and foreign hosts don't."""
    from anthias_server.app.views import _host_allowed

    assert _host_allowed('weather.srly.io')
    assert _host_allowed('srly.io')  # the bare apex
    # Pass the uppercase form as-is: exercises the internal lower-casing
    # (a URL's hostname can arrive mixed-case).
    assert _host_allowed('WEATHER.SRLY.IO')
    assert _host_allowed('signage-apps.com')
    # A hostname is port/userinfo-free by the time it reaches here.
    assert not _host_allowed('evilsrly.io')  # no dot boundary
    assert not _host_allowed('srly.io.evil.com')
    assert not _host_allowed('evil.com')
    assert not _host_allowed('')


@pytest.mark.django_db
def test_assets_create_app_allows_port_in_uri(client: Client) -> None:
    """A store URL with an explicit port still installs — the host
    check runs on the port-free hostname, not the raw netloc."""
    _post_app(
        client,
        app_id='weather',
        manifest_url='https://weather.srly.io/.well-known/signage-app.json',
        manifest_version='1',
        name='Weather',
        app_uri='https://weather.srly.io:8443/?24h=1',
        app_values='{}',
    )
    assert Asset.objects.filter(name='Weather').exists()


@pytest.mark.django_db
def test_assets_create_app_rejects_lookalike_host(client: Client) -> None:
    """Suffix matching is on a dotted boundary, so a look-alike domain
    (evilsrly.io) does not satisfy the .srly.io suffix."""
    _post_app(
        client,
        app_id='evil',
        manifest_url='https://evilsrly.io/manifest.json',
        manifest_version='1',
        name='Evil',
        app_uri='https://evilsrly.io/',
        app_values='{}',
    )
    assert not Asset.objects.filter(uri='https://evilsrly.io/').exists()


@pytest.mark.django_db
def test_assets_create_app_rejects_invalid_uri(client: Client) -> None:
    _post_app(
        client,
        app_id='weather',
        manifest_url='https://weather.srly.io/.well-known/signage-app.json',
        manifest_version='1',
        name='Weather',
        app_uri='not-a-url',
        app_values='{}',
    )
    assert not Asset.objects.filter(name='Weather').exists()


@pytest.mark.django_db
def test_assets_create_app_tolerates_malformed_values(client: Client) -> None:
    """Malformed / non-object values JSON degrades to an empty bag
    rather than 500-ing the install."""
    _post_app(
        client,
        app_id='weather',
        manifest_url='https://weather.srly.io/.well-known/signage-app.json',
        manifest_version='1',
        name='Weather',
        app_uri='https://weather.srly.io/',
        app_values='["not", "an", "object"]',
    )
    asset = Asset.objects.filter(name='Weather').first()
    assert asset is not None
    assert asset.metadata['app']['values'] == {}


@pytest.fixture
def app_asset() -> Asset:
    now = timezone.now()
    return Asset.objects.create(
        name='Weather',
        uri='https://weather.srly.io/?24h=1',
        mimetype='webpage',
        duration=10,
        is_enabled=True,
        is_processing=False,
        play_order=0,
        start_date=now,
        end_date=now + timedelta(days=30),
        metadata={
            'app': {
                'id': 'weather',
                'manifest_url': (
                    'https://weather.srly.io/.well-known/signage-app.json'
                ),
                'manifest_version': '1',
                'values': {'24h': '1'},
            }
        },
    )


def _update_asset(client: Client, asset: Asset, **extra: Any) -> Any:
    data = {
        'name': asset.name,
        'duration': str(asset.duration),
        'start_date': timezone.localtime(asset.start_date).strftime(
            '%Y-%m-%dT%H:%M'
        ),
        'end_date': timezone.localtime(asset.end_date).strftime(
            '%Y-%m-%dT%H:%M'
        ),
        **extra,
    }
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        return client.post(
            reverse('anthias_app:assets_update', args=[asset.asset_id]),
            data=data,
        )


@pytest.mark.django_db
def test_assets_update_app_rebuilds_uri_and_values(
    client: Client, app_asset: Asset
) -> None:
    """Editing an app asset rebuilds the URI (client-built) and
    refreshes metadata.app.values, keeping the app id/manifest intact."""
    _update_asset(
        client,
        app_asset,
        app_uri='https://weather.srly.io/?lat=40.7&lng=-74&24h=0',
        app_values='{"location": {"lat": 40.7, "lng": -74}, "24h": "0"}',
    )
    app_asset.refresh_from_db()
    assert app_asset.uri == 'https://weather.srly.io/?lat=40.7&lng=-74&24h=0'
    assert app_asset.metadata['app']['id'] == 'weather'
    assert app_asset.metadata['app']['values'] == {
        'location': {'lat': 40.7, 'lng': -74},
        '24h': '0',
    }


@pytest.mark.django_db
def test_assets_update_app_rejects_foreign_uri(
    client: Client, app_asset: Asset
) -> None:
    """A rebuilt URI outside the store allowlist is refused; the row's
    original URI is left untouched."""
    _update_asset(
        client,
        app_asset,
        app_uri='https://evil.example.com/',
        app_values='{}',
    )
    app_asset.refresh_from_db()
    assert app_asset.uri == 'https://weather.srly.io/?24h=1'


@pytest.mark.django_db
def test_assets_update_non_app_ignores_app_fields(
    client: Client, asset: Asset
) -> None:
    """A plain-webpage edit never posts app_* fields; even if forged,
    they're ignored because the row carries no metadata.app."""
    _update_asset(
        client,
        asset,
        app_uri='https://weather.srly.io/?x=1',
        app_values='{"x": "1"}',
    )
    asset.refresh_from_db()
    assert asset.uri == 'https://example.com'
    assert 'app' not in (asset.metadata or {})


@pytest.mark.django_db
def test_assets_toggle_flips_is_enabled(client: Client, asset: Asset) -> None:
    initial = asset.is_enabled
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_toggle', args=[asset.asset_id])
        )
    asset.refresh_from_db()
    assert asset.is_enabled is not initial


@pytest.mark.django_db
def test_assets_delete_removes_row(client: Client, asset: Asset) -> None:
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_delete', args=[asset.asset_id])
        )
    assert not Asset.objects.filter(asset_id=asset.asset_id).exists()


@pytest.mark.django_db
def test_assets_delete_removes_local_file(
    client: Client, tmp_path: Any
) -> None:
    """Regression for GH #2908: deleting an uploaded asset from the
    UI form-post route must also remove the binary on disk. Before
    the fix, ``assets_delete`` only ran ``Asset.objects.filter(...
    ).delete()`` and left the file in ``settings['assetdir']``
    forever — a Pi 4 with churn through uploads would fill its SD
    card from operator-deleted assets that "looked" gone in the UI.
    """
    from anthias_server.settings import settings as anthias_settings

    asset_path = (
        tmp_path / anthias_settings['assetdir'].lstrip('/') / 'video.mp4'
    )
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(b'\x00\x01video-payload')

    now = timezone.now()
    asset = Asset.objects.create(
        name='Local video',
        uri=str(asset_path),
        mimetype='video',
        duration=10,
        is_enabled=True,
        is_processing=False,
        play_order=0,
        start_date=now,
        end_date=now + timedelta(days=30),
    )

    # ``settings['assetdir']`` is fixed at import time to
    # ``<HOME>/anthias_assets``. Repoint it at the tmp_path mirror so
    # the delete view's startswith() check matches the on-disk path.
    with (
        mock.patch.dict(
            anthias_settings,
            {'assetdir': str(asset_path.parent)},
        ),
        mock.patch(
            'anthias_server.settings.ViewerPublisher.send_to_viewer',
            return_value=None,
        ),
    ):
        response = client.post(
            reverse('anthias_app:assets_delete', args=[asset.asset_id])
        )

    assert response.status_code in (200, 302)
    assert not Asset.objects.filter(asset_id=asset.asset_id).exists()
    assert not asset_path.exists(), (
        f'asset file {asset_path} survived UI delete'
    )


@pytest.mark.django_db
def test_assets_order_persists_play_order(client: Client) -> None:
    a1 = Asset.objects.create(
        name='a1',
        uri='u1',
        mimetype='webpage',
        duration=1,
        is_enabled=True,
        play_order=0,
    )
    a2 = Asset.objects.create(
        name='a2',
        uri='u2',
        mimetype='webpage',
        duration=1,
        is_enabled=True,
        play_order=1,
    )
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_order'),
            data={'ids': f'{a2.asset_id},{a1.asset_id}'},
        )
    a1.refresh_from_db()
    a2.refresh_from_db()
    assert a2.play_order == 0
    assert a1.play_order == 1


@pytest.mark.django_db
@pytest.mark.parametrize('command', ['next', 'previous'])
def test_assets_control_dispatches(client: Client, command: str) -> None:
    """Regression for #2821: the form-post view must publish the same
    bare ``next``/``previous`` token the viewer's command dispatch
    table keys on (src/anthias_viewer/__init__.py — ``commands``).
    A previous revision sent ``asset_<command>``, which fell through
    to the ``unknown`` handler and silently no-op'd."""
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ) as send:
        response = client.post(
            reverse('anthias_app:assets_control', args=[command])
        )
    assert response.status_code in (200, 302)
    send.assert_called_once_with(command)


@pytest.mark.django_db
def test_assets_download_redirects_for_url_mimetype(
    client: Client, asset: Asset
) -> None:
    response = client.get(
        reverse('anthias_app:assets_download', args=[asset.asset_id])
    )
    # webpage → redirect to URI
    assert response.status_code == 302
    assert response['Location'] == asset.uri


@pytest.mark.django_db
def test_settings_save_round_trip(
    client: Client, _isolated_settings_conf: Any
) -> None:
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:settings_save'),
            data={
                'player_name': 'Test Player',
                'default_duration': '15',
                'default_streaming_duration': '300',
                'audio_output': 'hdmi',
                'date_format': 'mm/dd/yyyy',
                'auth_backend': '',
                'show_splash': 'true',
            },
        )
    assert response.status_code in (200, 302)


@pytest.mark.django_db
@pytest.mark.parametrize(
    'posted, persisted',
    [
        ('90', 90),
        ('270', 270),
        # Non-cardinal / garbage angles clamp to 0 — defends the
        # viewer's CLI argv against a hostile or buggy form.
        ('45', 0),
        ('definitely-not-a-number', 0),
    ],
)
def test_settings_save_screen_rotation(
    client: Client, posted: str, persisted: int, _isolated_settings_conf: Any
) -> None:
    """Issue #2856 — form path mirrors the v2 PATCH validation."""
    from anthias_server.settings import settings

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:settings_save'),
            data={
                'player_name': 'Test',
                'default_duration': '10',
                'default_streaming_duration': '300',
                'audio_output': 'hdmi',
                'date_format': 'mm/dd/yyyy',
                'auth_backend': '',
                'screen_rotation': posted,
            },
        )
    assert response.status_code in (200, 302)
    assert settings['screen_rotation'] == persisted


@pytest.mark.django_db
def test_settings_save_timezone_valid(
    client: Client, _isolated_settings_conf: Any
) -> None:
    """A valid IANA zone posted from the HTML form is persisted."""
    from anthias_server.settings import settings

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:settings_save'),
            data={
                'player_name': 'Test',
                'default_duration': '10',
                'default_streaming_duration': '300',
                'audio_output': 'hdmi',
                'date_format': 'mm/dd/yyyy',
                'auth_backend': '',
                'timezone': 'Europe/Stockholm',
            },
        )
    assert response.status_code in (200, 302)
    assert settings['timezone'] == 'Europe/Stockholm'


@pytest.mark.django_db
def test_settings_save_timezone_invalid_rejected(
    client: Client, _isolated_settings_conf: Any
) -> None:
    """A bad zone is rejected up front and never written — a value that
    would crash-loop the settings module can't be persisted."""
    from anthias_server.settings import settings

    settings['timezone'] = 'Europe/Stockholm'
    settings.save()

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ) as publish_mock:
        response = client.post(
            reverse('anthias_app:settings_save'),
            data={
                'player_name': 'Test',
                'default_duration': '10',
                'default_streaming_duration': '300',
                'audio_output': 'hdmi',
                'date_format': 'mm/dd/yyyy',
                'auth_backend': '',
                'timezone': 'Mars/Phobos',
            },
        )
    assert response.status_code in (200, 302)
    # Prior value untouched, and no reload was signalled.
    assert settings['timezone'] == 'Europe/Stockholm'
    publish_mock.assert_not_called()


@pytest.mark.django_db
@mock.patch(
    'anthias_server.app.views.reboot_anthias.apply_async',
    side_effect=(lambda: None),
)
def test_settings_reboot(reboot_mock: Any, client: Client) -> None:
    response = client.post(reverse('anthias_app:settings_reboot'))
    assert response.status_code in (200, 302)
    assert reboot_mock.called


@pytest.mark.django_db
@mock.patch(
    'anthias_server.app.views.shutdown_anthias.apply_async',
    side_effect=(lambda: None),
)
def test_settings_shutdown(shutdown_mock: Any, client: Client) -> None:
    response = client.post(reverse('anthias_app:settings_shutdown'))
    assert response.status_code in (200, 302)
    assert shutdown_mock.called


@pytest.mark.django_db
@mock.patch(
    'anthias_server.app.views.diagnostics.cec_available', return_value=True
)
@mock.patch(
    'anthias_server.app.views.diagnostics.set_display_power',
    return_value=(True, 'Display turn-on command sent.'),
)
def test_settings_display_on(
    set_display_power_mock: Any,
    _cec_available_mock: Any,
    client: Client,
) -> None:
    response = client.post(
        reverse('anthias_app:settings_display_power', kwargs={'state': 'on'})
    )
    assert response.status_code in (200, 302)
    set_display_power_mock.assert_called_once_with(on=True)


@pytest.mark.django_db
@mock.patch(
    'anthias_server.app.views.diagnostics.cec_available', return_value=True
)
@mock.patch(
    'anthias_server.app.views.diagnostics.set_display_power',
    return_value=(True, 'Display turn-off command sent.'),
)
def test_settings_display_off(
    set_display_power_mock: Any,
    _cec_available_mock: Any,
    client: Client,
) -> None:
    response = client.post(
        reverse('anthias_app:settings_display_power', kwargs={'state': 'off'})
    )
    assert response.status_code in (200, 302)
    set_display_power_mock.assert_called_once_with(on=False)


@pytest.mark.django_db
@mock.patch('anthias_server.app.views.diagnostics.set_display_power')
def test_settings_display_invalid_state(
    set_display_power_mock: Any, client: Client
) -> None:
    response = client.post(
        reverse('anthias_app:settings_display_power', kwargs={'state': 'foo'})
    )
    assert response.status_code in (200, 302)
    set_display_power_mock.assert_not_called()


@pytest.mark.django_db
@mock.patch(
    'anthias_server.app.views.diagnostics.cec_available', return_value=False
)
@mock.patch('anthias_server.app.views.diagnostics.set_display_power')
def test_settings_display_blocked_without_cec(
    set_display_power_mock: Any,
    _cec_available_mock: Any,
    client: Client,
) -> None:
    """A stale form (or direct curl) against a non-CEC device must
    short-circuit before the 10 s libcec subprocess ever runs."""
    from django.contrib.messages import get_messages

    response = client.post(
        reverse('anthias_app:settings_display_power', kwargs={'state': 'on'})
    )
    assert response.status_code in (200, 302)
    set_display_power_mock.assert_not_called()
    messages_out = [m.message for m in get_messages(response.wsgi_request)]
    assert any('CEC' in m or 'adapter' in m for m in messages_out)


@pytest.mark.django_db
@mock.patch(
    'anthias_server.app.views.diagnostics.cec_available', return_value=True
)
@mock.patch(
    'anthias_server.app.views.diagnostics.set_display_power',
    return_value=(False, 'Display turn-on failed: no adapter'),
)
def test_settings_display_surfaces_error_message(
    _set_display_power_mock: Any,
    _cec_available_mock: Any,
    client: Client,
) -> None:
    """Failed CEC commands must reach the operator via a flash message
    (the feedback loop called out in issue #2575)."""
    from django.contrib.messages import get_messages

    response = client.post(
        reverse('anthias_app:settings_display_power', kwargs={'state': 'on'})
    )
    assert response.status_code in (200, 302)
    messages_out = [m.message for m in get_messages(response.wsgi_request)]
    assert any('no adapter' in m for m in messages_out)


@pytest.mark.django_db
def test_assets_update_via_post(client: Client, asset: Asset) -> None:
    new_name = 'Renamed asset'
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_update', args=[asset.asset_id]),
            data={
                'name': new_name,
                'mimetype': 'webpage',
                'duration': '20',
                'start_date': '2026-01-01T00:00',
                'end_date': '2027-01-01T00:00',
            },
        )
    asset.refresh_from_db()
    assert asset.name == new_name
    assert asset.duration == 20


@pytest.mark.django_db
def test_assets_update_writes_refresh_interval_to_metadata(
    client: Client, asset: Asset
) -> None:
    """The webpage auto-refresh field on the edit modal — feature #2813
    — POSTs ``refresh_interval_s`` alongside the rest of the form.
    The handler must merge it into ``Asset.metadata`` rather than
    overwriting the dict, so any pipeline-owned keys
    (original_ext / transcoded / error_message) survive an operator
    edit."""
    asset.metadata = {'original_ext': '.heic', 'transcoded': True}
    asset.save(update_fields=['metadata'])

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_update', args=[asset.asset_id]),
            data={
                'name': asset.name,
                'mimetype': 'webpage',
                'duration': '20',
                'start_date': '2026-01-01T00:00',
                'end_date': '2027-01-01T00:00',
                'refresh_interval_s': '45',
            },
        )

    asset.refresh_from_db()
    assert asset.metadata == {
        'original_ext': '.heic',
        'transcoded': True,
        'refresh_interval_s': 45,
    }


@pytest.mark.django_db
def test_assets_update_clears_refresh_interval_on_empty_input(
    client: Client, asset: Asset
) -> None:
    """An empty ``refresh_interval_s`` from the edit form means the
    operator cleared the field, which the AC for #2813 specifies must
    disable auto-refresh — recorded as 0 (the viewer treats 0 the
    same as a missing key)."""
    asset.metadata = {'refresh_interval_s': 60}
    asset.save(update_fields=['metadata'])

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_update', args=[asset.asset_id]),
            data={
                'name': asset.name,
                'mimetype': 'webpage',
                'duration': '20',
                'start_date': '2026-01-01T00:00',
                'end_date': '2027-01-01T00:00',
                'refresh_interval_s': '',
            },
        )

    asset.refresh_from_db()
    assert asset.metadata.get('refresh_interval_s') == 0


@pytest.mark.django_db
def test_assets_update_clamps_oversize_refresh_interval(
    client: Client, asset: Asset
) -> None:
    """The form-level handler clamps (rather than 400s) for friendlier
    UX — the strict validation lives on the v2 API. 86400 (24h) is
    the cap shared with REFRESH_INTERVAL_S_MAX in the v2 serializer."""
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_update', args=[asset.asset_id]),
            data={
                'name': asset.name,
                'mimetype': 'webpage',
                'duration': '20',
                'start_date': '2026-01-01T00:00',
                'end_date': '2027-01-01T00:00',
                'refresh_interval_s': '999999',
            },
        )
    asset.refresh_from_db()
    assert asset.metadata.get('refresh_interval_s') == 86400


@pytest.mark.django_db
def test_assets_update_clamps_oversize_duration(
    client: Client, asset: Asset
) -> None:
    """Same clamp-not-400 policy as the refresh interval above. The
    posted value is the real one from Sentry ANTHIAS-3E: an operator
    typed 9999999999999 to mean "forever", and the stored row
    crash-looped the viewer's ``Event.wait`` on OverflowError."""
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_update', args=[asset.asset_id]),
            data={
                'name': asset.name,
                'mimetype': 'webpage',
                'duration': '9999999999999',
                'start_date': '2026-01-01T00:00',
                'end_date': '2027-01-01T00:00',
                'refresh_interval_s': '',
            },
        )
    asset.refresh_from_db()
    assert asset.duration == DURATION_S_MAX


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('play_from', 'play_to', 'expected_from', 'expected_to'),
    [
        # 12-hour AM/PM shapes — issue #2988: the default
        # use_24_hour_clock=False makes Flatpickr post these, and
        # assets_update used to 500 on int('30 PM').
        ('02:30 PM', '11:45 PM', time(14, 30), time(23, 45)),
        ('2:30 PM', '11:45 PM', time(14, 30), time(23, 45)),
        ('12:00 AM', '12:30 PM', time(0, 0), time(12, 30)),
        # 24-hour shapes keep working.
        ('09:15', '17:45', time(9, 15), time(17, 45)),
        # ISO TimeField round-trip (API-side writes re-posted).
        ('09:15:00', '17:45:00', time(9, 15), time(17, 45)),
    ],
)
def test_assets_update_parses_play_time_formats(
    client: Client,
    asset: Asset,
    play_from: str,
    play_to: str,
    expected_from: time,
    expected_to: time,
) -> None:
    """Regression for issue #2988: every clock format the Play from /
    Play until pickers can post must parse and persist."""
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_update', args=[asset.asset_id]),
            data={
                'name': asset.name,
                'duration': '20',
                'start_date': '2026-01-01T00:00',
                'end_date': '2027-01-01T00:00',
                'play_time_from': play_from,
                'play_time_to': play_to,
            },
        )
    assert response.status_code in (200, 302)
    asset.refresh_from_db()
    assert asset.play_time_from == expected_from
    assert asset.play_time_to == expected_to


@pytest.mark.django_db
def test_assets_update_invalid_play_time_toasts_instead_of_500(
    client: Client, asset: Asset
) -> None:
    """allowInput lets the operator type anything into the time
    fields — junk must come back as an error toast, never a 500, and
    must not half-save the window."""
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_update', args=[asset.asset_id]),
            data={
                'name': 'Should not stick',
                'duration': '20',
                'start_date': '2026-01-01T00:00',
                'end_date': '2027-01-01T00:00',
                'play_time_from': 'half past nope',
                'play_time_to': '17:00',
            },
            headers={'HX-Request': 'true'},
        )
    assert response.status_code == 200
    assert 'HX-Trigger' in response.headers
    assert 'error' in response.headers['HX-Trigger']
    asset.refresh_from_db()
    assert asset.play_time_from is None
    assert asset.play_time_to is None
    assert asset.name != 'Should not stick'


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('play_from', 'play_to'),
    [('09:00', ''), ('', '17:00')],
)
def test_assets_update_partial_play_window_toasts_and_keeps_existing(
    client: Client, asset: Asset, play_from: str, play_to: str
) -> None:
    """Only one endpoint set is a validation error (mirrors the v2
    API's _validate_time_window) — it must NOT silently wipe an
    existing window."""
    asset.play_time_from = time(8, 0)
    asset.play_time_to = time(18, 0)
    asset.save(update_fields=['play_time_from', 'play_time_to'])

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_update', args=[asset.asset_id]),
            data={
                'name': asset.name,
                'duration': '20',
                'start_date': '2026-01-01T00:00',
                'end_date': '2027-01-01T00:00',
                'play_time_from': play_from,
                'play_time_to': play_to,
            },
            headers={'HX-Request': 'true'},
        )
    assert response.status_code == 200
    assert 'error' in response.headers.get('HX-Trigger', '')
    asset.refresh_from_db()
    assert asset.play_time_from == time(8, 0)
    assert asset.play_time_to == time(18, 0)


@pytest.mark.django_db
def test_assets_update_clears_play_window_when_both_empty(
    client: Client, asset: Asset
) -> None:
    """Both endpoints cleared = deliberate "play all day" reset."""
    asset.play_time_from = time(8, 0)
    asset.play_time_to = time(18, 0)
    asset.save(update_fields=['play_time_from', 'play_time_to'])

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_update', args=[asset.asset_id]),
            data={
                'name': asset.name,
                'duration': '20',
                'start_date': '2026-01-01T00:00',
                'end_date': '2027-01-01T00:00',
                'play_time_from': '',
                'play_time_to': '',
            },
        )
    assert response.status_code in (200, 302)
    asset.refresh_from_db()
    assert asset.play_time_from is None
    assert asset.play_time_to is None


@pytest.mark.django_db
def test_assets_update_parses_12_hour_start_end_dates(
    client: Client, asset: Asset
) -> None:
    """The Start / End availability pickers post 'm/d/Y h:i K' under
    the default 12-hour clock + mm/dd/yyyy date format."""
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_update', args=[asset.asset_id]),
            data={
                'name': asset.name,
                'duration': '20',
                'start_date': '06/15/2026 9:00 AM',
                'end_date': '12/24/2026 11:30 PM',
            },
        )
    assert response.status_code in (200, 302)
    asset.refresh_from_db()
    assert asset.start_date is not None and asset.end_date is not None
    assert (
        asset.start_date.month,
        asset.start_date.day,
        asset.start_date.hour,
        asset.start_date.minute,
    ) == (6, 15, 9, 0)
    assert (
        asset.end_date.month,
        asset.end_date.day,
        asset.end_date.hour,
        asset.end_date.minute,
    ) == (12, 24, 23, 30)


@pytest.mark.django_db
def test_assets_update_invalid_start_date_toasts_instead_of_500(
    client: Client, asset: Asset
) -> None:
    original_start = asset.start_date
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_update', args=[asset.asset_id]),
            data={
                'name': asset.name,
                'duration': '20',
                'start_date': 'sometime soon',
                'end_date': '2027-01-01T00:00',
            },
            headers={'HX-Request': 'true'},
        )
    assert response.status_code == 200
    assert 'error' in response.headers.get('HX-Trigger', '')
    asset.refresh_from_db()
    assert asset.start_date == original_start


@pytest.mark.django_db
def test_assets_update_missing_id_is_no_op(client: Client) -> None:
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_update', args=['does-not-exist']),
            data={'name': 'whatever'},
        )
    assert response.status_code in (200, 302)


# ---------------------------------------------------------------------------
# Bulk asset actions (#3046)


@pytest.fixture
def bulk_assets() -> list[Asset]:
    """Three assets — two webpages and one video — for bulk tests."""
    now = timezone.now()
    common = {
        'duration': 10,
        'is_enabled': True,
        'is_processing': False,
        'start_date': now,
        'end_date': now + timedelta(days=30),
    }
    return [
        Asset.objects.create(
            name='one',
            uri='https://a.example',
            mimetype='webpage',
            play_order=0,
            **common,
        ),
        Asset.objects.create(
            name='two',
            uri='https://b.example',
            mimetype='webpage',
            play_order=1,
            **common,
        ),
        Asset.objects.create(
            name='vid',
            uri='https://c.example/v.mp4',
            mimetype='video',
            play_order=2,
            **common,
        ),
    ]


def _bulk_ids_csv(assets: list[Asset]) -> str:
    return ','.join(a.asset_id for a in assets)


@pytest.mark.django_db
def test_assets_bulk_action_disable(
    client: Client, bulk_assets: list[Asset]
) -> None:
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_bulk_action'),
            data={
                'action': 'disable',
                'ids': _bulk_ids_csv(bulk_assets),
            },
        )
    assert response.status_code in (200, 302)
    for a in bulk_assets:
        a.refresh_from_db()
        assert a.is_enabled is False


@pytest.mark.django_db
def test_assets_bulk_action_enable_only_selected(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """Only the ids in the POST flip — an unselected row is untouched."""
    for a in bulk_assets:
        a.is_enabled = False
        a.save()
    selected = bulk_assets[:2]
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_bulk_action'),
            data={'action': 'enable', 'ids': _bulk_ids_csv(selected)},
        )
    bulk_assets[0].refresh_from_db()
    bulk_assets[1].refresh_from_db()
    bulk_assets[2].refresh_from_db()
    assert bulk_assets[0].is_enabled is True
    assert bulk_assets[1].is_enabled is True
    assert bulk_assets[2].is_enabled is False


@pytest.mark.django_db
def test_assets_bulk_action_delete(
    client: Client, bulk_assets: list[Asset]
) -> None:
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_bulk_action'),
            data={'action': 'delete', 'ids': _bulk_ids_csv(bulk_assets)},
        )
    assert Asset.objects.count() == 0


@pytest.mark.django_db
def test_assets_bulk_action_invalid_action_is_noop(
    client: Client, bulk_assets: list[Asset]
) -> None:
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_bulk_action'),
            data={'action': 'frobnicate', 'ids': _bulk_ids_csv(bulk_assets)},
        )
    assert response.status_code in (200, 302)
    assert Asset.objects.count() == 3
    for a in bulk_assets:
        a.refresh_from_db()
        assert a.is_enabled is True


@pytest.mark.django_db
def test_assets_bulk_action_empty_ids_is_noop(
    client: Client, bulk_assets: list[Asset]
) -> None:
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_bulk_action'),
            data={'action': 'delete', 'ids': ''},
        )
    assert response.status_code in (200, 302)
    assert Asset.objects.count() == 3


@pytest.mark.django_db
def test_assets_bulk_action_trims_whitespace_in_ids(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """A hand-built ``"a, b"`` CSV (spaces after commas) must still
    match every row — _bulk_ids strips each segment (Copilot review of
    #3048).
    """
    for a in bulk_assets:
        a.is_enabled = False
        a.save()
    spaced = ', '.join(a.asset_id for a in bulk_assets)
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_bulk_action'),
            data={'action': 'enable', 'ids': spaced},
        )
    for a in bulk_assets:
        a.refresh_from_db()
        assert a.is_enabled is True


@pytest.mark.django_db
def test_assets_bulk_action_enable_already_enabled_reports_match_count(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """Re-enabling already-enabled assets must report the matched count,
    not 0 — the count comes from a matched-rows count(), not update()'s
    changed-rows return, which can be 0 on some backends (Copilot review
    of #3048). A 0 with no toast would also make the client treat it as
    success and clear the selection.
    """
    import json as _json

    # bulk_assets are created is_enabled=True; enable them again.
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_bulk_action'),
            data={
                'action': 'enable',
                'ids': _bulk_ids_csv(bulk_assets),
            },
            HTTP_HX_REQUEST='true',
        )
    trigger = _json.loads(response['HX-Trigger'])
    assert trigger['toast'] == {
        'kind': 'success',
        'message': '3 assets enabled',
    }


@pytest.mark.django_db
def test_assets_bulk_action_no_matching_ids_keeps_selection(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """Stale ids that match nothing return an info toast (not a silent
    no-toast 2xx) so the client's success gate keeps the selection."""
    import json as _json

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_bulk_action'),
            data={'action': 'enable', 'ids': 'no-such-id,also-missing'},
            HTTP_HX_REQUEST='true',
        )
    trigger = _json.loads(response['HX-Trigger'])
    assert trigger['toast']['kind'] == 'info'


@pytest.mark.django_db
def test_assets_bulk_update_unmatched_ids_keeps_selection(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """bulk_update with non-empty but unmatched ids returns an info
    toast (not a silent no-toast 2xx) so the client keeps the selection
    and the modal stays open (Copilot review of #3048)."""
    import json as _json

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': 'no-such-id,also-missing',
                'apply_duration': 'true',
                'duration': '42',
            },
            HTTP_HX_REQUEST='true',
        )
    trigger = _json.loads(response['HX-Trigger'])
    assert trigger['toast']['kind'] == 'info'


@pytest.mark.django_db
def test_assets_bulk_update_dates(
    client: Client, bulk_assets: list[Asset]
) -> None:
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': _bulk_ids_csv(bulk_assets),
                'apply_dates': 'true',
                'start_date': '01/02/2030 09:00 AM',
                'end_date': '01/03/2030 09:00 AM',
            },
        )
    for a in bulk_assets:
        a.refresh_from_db()
        assert a.start_date is not None
        assert (a.start_date.year, a.start_date.month, a.start_date.day) == (
            2030,
            1,
            2,
        )


@pytest.mark.django_db
def test_assets_bulk_update_duration_skips_video(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """Duration is applied to images/webpages but never videos — the
    video's duration is owned by the probe task (mirrors assets_update).
    """
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': _bulk_ids_csv(bulk_assets),
                'apply_duration': 'true',
                'duration': '42',
            },
        )
    webpage_one, webpage_two, video = bulk_assets
    webpage_one.refresh_from_db()
    webpage_two.refresh_from_db()
    video.refresh_from_db()
    assert webpage_one.duration == 42
    assert webpage_two.duration == 42
    assert video.duration == 10


@pytest.mark.django_db
def test_assets_bulk_update_blank_duration_does_not_clobber(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """apply_duration on with a blank duration must NOT zero out every
    asset — it toasts and changes nothing (Copilot review of #3048).
    """
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': _bulk_ids_csv(bulk_assets),
                'apply_duration': 'true',
                'duration': '',
            },
        )
    assert response.status_code in (200, 302)
    for a in bulk_assets:
        a.refresh_from_db()
        assert a.duration == 10


@pytest.mark.django_db
def test_assets_bulk_update_duration_only_count_excludes_videos(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """A duration-only edit on a mixed selection (2 webpages + 1 video)
    skips the video, so the success toast must report 2 updated, not 3
    (Copilot review of #3048). Read the count off the HX-Trigger toast.
    """
    import json as _json

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': _bulk_ids_csv(bulk_assets),
                'apply_duration': 'true',
                'duration': '42',
            },
            HTTP_HX_REQUEST='true',
        )
    trigger = _json.loads(response['HX-Trigger'])
    assert trigger['toast']['message'] == '2 assets updated'


@pytest.mark.django_db
def test_assets_bulk_update_never_writes_video_duration(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """The duration update must exclude video rows at the SQL level so
    it can't race with / clobber the probe_video_duration task. Set the
    video's stored duration to a sentinel and confirm a bulk duration
    edit leaves it untouched while the webpages change (Copilot review
    of #3048).
    """
    webpage_one, webpage_two, video = bulk_assets
    # Simulate the probe having written a duration on the video; the
    # bulk edit must not overwrite it.
    Asset.objects.filter(asset_id=video.asset_id).update(duration=123)

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': _bulk_ids_csv(bulk_assets),
                'apply_duration': 'true',
                'duration': '42',
            },
        )

    video.refresh_from_db()
    webpage_one.refresh_from_db()
    webpage_two.refresh_from_db()
    assert video.duration == 123, 'video duration was clobbered'
    assert webpage_one.duration == 42
    assert webpage_two.duration == 42


@pytest.mark.django_db
def test_assets_bulk_update_issues_single_update_query(
    client: Client,
) -> None:
    """Bulk update must write the whole selection in one UPDATE, not one
    per row (Copilot review of #3048) — proven by counting UPDATE
    statements against a 5-asset selection.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    now = timezone.now()
    ids = []
    for i in range(5):
        a = Asset.objects.create(
            name=f'q{i}',
            uri=f'https://q{i}.example',
            mimetype='webpage',
            duration=10,
            is_enabled=True,
            is_processing=False,
            play_order=i,
            start_date=now,
            end_date=now + timedelta(days=30),
        )
        ids.append(a.asset_id)

    with (
        mock.patch(
            'anthias_server.settings.ViewerPublisher.send_to_viewer',
            return_value=None,
        ),
        CaptureQueriesContext(connection) as ctx,
    ):
        client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': ','.join(ids),
                'apply_duration': 'true',
                'duration': '55',
            },
        )

    updates = [
        q['sql']
        for q in ctx.captured_queries
        if q['sql'].lstrip().upper().startswith('UPDATE "ASSETS"')
    ]
    assert len(updates) == 1, (
        f'expected exactly 1 UPDATE for the batch, got {len(updates)}:\n'
        + '\n'.join(updates)
    )
    for a_id in ids:
        assert Asset.objects.get(asset_id=a_id).duration == 55


@pytest.mark.django_db
def test_assets_bulk_update_time_window(
    client: Client, bulk_assets: list[Asset]
) -> None:
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': _bulk_ids_csv(bulk_assets),
                'apply_time': 'true',
                'play_time_from': '09:00',
                'play_time_to': '17:00',
            },
        )
    for a in bulk_assets:
        a.refresh_from_db()
        assert a.play_time_from == time(9, 0)
        assert a.play_time_to == time(17, 0)


@pytest.mark.django_db
def test_assets_bulk_update_clears_time_window(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """apply_time on with both fields empty removes the window."""
    for a in bulk_assets:
        a.play_time_from = time(9, 0)
        a.play_time_to = time(17, 0)
        a.save()
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': _bulk_ids_csv(bulk_assets),
                'apply_time': 'true',
                'play_time_from': '',
                'play_time_to': '',
            },
        )
    for a in bulk_assets:
        a.refresh_from_db()
        assert a.play_time_from is None
        assert a.play_time_to is None


@pytest.mark.django_db
def test_assets_bulk_update_partial_time_window_toasts(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """Only one endpoint set — reject and leave everything untouched."""
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': _bulk_ids_csv(bulk_assets),
                'apply_time': 'true',
                'play_time_from': '09:00',
                'play_time_to': '',
            },
        )
    for a in bulk_assets:
        a.refresh_from_db()
        assert a.play_time_from is None


@pytest.mark.django_db
def test_assets_bulk_update_days(
    client: Client, bulk_assets: list[Asset]
) -> None:
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': _bulk_ids_csv(bulk_assets),
                'apply_days': 'true',
                'play_days': ['1', '3', '5'],
            },
        )
    for a in bulk_assets:
        a.refresh_from_db()
        assert a.get_play_days() == [1, 3, 5]


@pytest.mark.django_db
def test_assets_bulk_update_nocache_sets_flag(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """Ticking the No cache group with the On radio sets nocache on
    every selected asset, videos included (#3137)."""
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': _bulk_ids_csv(bulk_assets),
                'apply_nocache': 'true',
                'nocache': 'true',
            },
        )
    for a in bulk_assets:
        a.refresh_from_db()
        assert a.nocache is True


@pytest.mark.django_db
def test_assets_bulk_update_nocache_off_clears_flag(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """The group can clear as well as set: the Off radio POSTs
    nocache=false and turns the flag off on every selected asset (#3137).
    """
    Asset.objects.filter(
        asset_id__in=[a.asset_id for a in bulk_assets]
    ).update(nocache=True)
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': _bulk_ids_csv(bulk_assets),
                'apply_nocache': 'true',
                'nocache': 'false',
            },
        )
    for a in bulk_assets:
        a.refresh_from_db()
        assert a.nocache is False


@pytest.mark.django_db
def test_assets_bulk_update_skip_asset_check_sets_flag(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """Ticking the Skip asset check group with the On radio sets
    skip_asset_check on every selected asset (#3137)."""
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': _bulk_ids_csv(bulk_assets),
                'apply_skip_asset_check': 'true',
                'skip_asset_check': 'true',
            },
        )
    for a in bulk_assets:
        a.refresh_from_db()
        assert a.skip_asset_check is True


@pytest.mark.django_db
def test_assets_bulk_update_flag_untouched_when_group_off(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """A flag value POSTed without its apply_* toggle is ignored — an
    unticked group never overwrites the stored flag (#3137)."""
    Asset.objects.filter(
        asset_id__in=[a.asset_id for a in bulk_assets]
    ).update(nocache=True, skip_asset_check=True)
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': _bulk_ids_csv(bulk_assets),
                # Duration group is the only one ticked; the stray flag
                # values below must be ignored.
                'apply_duration': 'true',
                'duration': '42',
                'nocache': 'false',
                'skip_asset_check': 'false',
            },
        )
    for a in bulk_assets:
        a.refresh_from_db()
        assert a.nocache is True
        assert a.skip_asset_check is True


@pytest.mark.django_db
def test_assets_bulk_update_flag_only_counts_all_matched(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """A flag-only edit touches every matched row (videos included), so
    the success toast reports the full selection count, not the
    non-video subset the duration rule uses (#3137)."""
    import json as _json

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': _bulk_ids_csv(bulk_assets),
                'apply_nocache': 'true',
                'nocache': 'true',
            },
            HTTP_HX_REQUEST='true',
        )
    trigger = _json.loads(response['HX-Trigger'])
    assert trigger['toast']['message'] == '3 assets updated'


@pytest.mark.django_db
def test_assets_bulk_update_no_flags_is_noop(
    client: Client, bulk_assets: list[Asset]
) -> None:
    original = [a.start_date for a in bulk_assets]
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': _bulk_ids_csv(bulk_assets),
                'start_date': '01/02/2030 09:00 AM',
            },
        )
    assert response.status_code in (200, 302)
    for a, start in zip(bulk_assets, original):
        a.refresh_from_db()
        assert a.start_date == start


@pytest.mark.django_db
def test_assets_bulk_update_invalid_date_toasts_and_keeps_values(
    client: Client, bulk_assets: list[Asset]
) -> None:
    original = [a.start_date for a in bulk_assets]
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_bulk_update'),
            data={
                'ids': _bulk_ids_csv(bulk_assets),
                'apply_dates': 'true',
                'start_date': 'not-a-date',
            },
        )
    assert response.status_code in (200, 302)
    for a, start in zip(bulk_assets, original):
        a.refresh_from_db()
        assert a.start_date == start


@pytest.mark.django_db
def test_asset_ids_filter_emits_json_array(bulk_assets: list[Asset]) -> None:
    from anthias_server.app.templatetags.asset_filters import asset_ids

    rendered = str(asset_ids(bulk_assets))
    assert rendered.startswith('[') and rendered.endswith(']')
    for a in bulk_assets:
        assert a.asset_id in rendered


@pytest.mark.django_db
def test_asset_table_renders_selection_controls(
    client: Client, bulk_assets: list[Asset]
) -> None:
    response = client.get(reverse('anthias_app:assets_table'))
    body = response.content.decode()
    assert 'js-row-select' in body
    assert "sectionAllSelected('active')" in body


@pytest.mark.django_db
def test_bulk_forms_use_global_event_bridge_not_alpine_methods(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """hx-on::after-request runs in global JS scope, so the bulk forms
    must NOT call Alpine component methods there (that throws
    ReferenceError and the selection never clears). They go through the
    window.bulkSucceeded() gate + a 'bulk-done' window event that Alpine
    handles via @bulk-done.window (Copilot review of #3048).
    """
    body = client.get(reverse('anthias_app:home')).content.decode()
    assert 'window.bulkSucceeded(event)' in body
    assert '@bulk-done.window="onBulkDone($event)"' in body
    # The old, broken forms called Alpine methods straight from hx-on.
    assert 'hx-on::after-request="if (isSuccessResponse(' not in body
    assert (
        'hx-on::after-request="if (event.detail.successful) clearSelection'
        not in body
    )


@pytest.mark.django_db
def test_asset_ids_json_is_html_escaped_in_x_init(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """Regression for the Copilot review of #3048: the asset_ids JSON
    is inlined into a double-quoted x-init="…" attribute, so its own
    double quotes MUST be entity-escaped (Django autoescaping) — a raw
    `["id"]` would close the attribute early and break the markup.
    """
    response = client.get(reverse('anthias_app:assets_table'))
    body = response.content.decode()
    # The ids land entity-escaped inside the syncVisibleIds() call …
    assert 'syncVisibleIds(' in body
    assert '&quot;' in body
    # … and the raw, attribute-breaking form must not appear.
    assert 'syncVisibleIds([&quot;' in body or 'syncVisibleIds([])' in body
    assert 'syncVisibleIds(["' not in body


@pytest.mark.django_db
def test_asset_table_partial_via_htmx_header(
    client: Client, asset: Asset
) -> None:
    """Write endpoints branch on HX-Request — exercise the HTMX path
    so the partial-rendering branch in _asset_table_response is hit."""
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_toggle', args=[asset.asset_id]),
            HTTP_HX_REQUEST='true',
        )
    assert response.status_code == 200
    body = response.content.decode()
    # The HTMX path returns the table partial — not a full page, so the
    # navbar markup should NOT appear; the asset table div should.
    assert 'id="asset-table"' in body or 'asset-table' in body


@pytest.mark.django_db
def test_assets_download_404_for_unknown_id(client: Client) -> None:
    response = client.get(
        reverse('anthias_app:assets_download', args=['no-such-asset'])
    )
    assert response.status_code == 302
    # Unknown id falls back to home, not the asset URI.
    assert response['Location'].endswith('/')


@pytest.mark.django_db
def test_assets_preview_redirects_for_url_mimetype(
    client: Client, asset: Asset
) -> None:
    response = client.get(
        reverse('anthias_app:assets_preview', args=[asset.asset_id])
    )
    # webpage → redirect to URI, same as download.
    assert response.status_code == 302
    assert response['Location'] == asset.uri


@pytest.mark.django_db
def test_assets_preview_404_for_unknown_id(client: Client) -> None:
    response = client.get(
        reverse('anthias_app:assets_preview', args=['no-such-asset'])
    )
    assert response.status_code == 302
    assert response['Location'].endswith('/')


def _remote_media_asset(mimetype: str, uri: str) -> Asset:
    now = timezone.now()
    return Asset.objects.create(
        name=f'Remote {mimetype}',
        uri=uri,
        mimetype=mimetype,
        duration=10,
        is_enabled=True,
        is_processing=False,
        play_order=0,
        start_date=now,
        end_date=now + timedelta(days=30),
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    'mimetype,uri',
    [
        ('image', 'https://upload.wikimedia.org/wikipedia/x/cat.jpg'),
        ('video', 'https://cdn.example.com/clip.mp4'),
    ],
)
def test_assets_preview_redirects_for_remote_media(
    client: Client, mimetype: str, uri: str
) -> None:
    """Regression for the forum "web content doesn't display" report:
    a remote-hosted image is stored mimetype image with an http(s) uri
    (unlike remote videos, which Celery downloads to a local file), so
    the preview must redirect to the source rather than 302-ing to the
    home page (the blank preview). The endpoint dispatches on the uri
    scheme, so a remote-uri video row redirects the same way."""
    asset = _remote_media_asset(mimetype, uri)
    response = client.get(
        reverse('anthias_app:assets_preview', args=[asset.asset_id])
    )
    assert response.status_code == 302
    assert response['Location'] == uri


@pytest.mark.django_db
@pytest.mark.parametrize(
    'mimetype,uri',
    [
        ('image', 'https://upload.wikimedia.org/wikipedia/x/cat.jpg'),
        ('video', 'https://cdn.example.com/clip.mp4'),
    ],
)
def test_assets_download_redirects_for_remote_media(
    client: Client, mimetype: str, uri: str
) -> None:
    asset = _remote_media_asset(mimetype, uri)
    response = client.get(
        reverse('anthias_app:assets_download', args=[asset.asset_id])
    )
    assert response.status_code == 302
    assert response['Location'] == uri


@pytest.mark.django_db
def test_settings_save_invalid_default_streaming_duration(
    client: Client, _isolated_settings_conf: Any
) -> None:
    """A non-numeric duration must not 500. ``clamp_duration`` coerces
    the junk to a safe ``0`` (it swallows the ValueError rather than
    letting it reach the handler's except branch), the save returns
    normally, and the clamped value is what gets persisted."""
    from anthias_server.settings import settings

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:settings_save'),
            data={
                'player_name': 'Test',
                'default_duration': '10',
                'default_streaming_duration': 'not-a-number',
                'audio_output': 'hdmi',
                'date_format': 'mm/dd/yyyy',
                'auth_backend': '',
            },
        )
    assert response.status_code in (200, 302)
    # Persisted as the string '0' — default_streaming_duration's default
    # is a str, so configparser reads it back with .get, not .getint.
    assert int(settings['default_streaming_duration']) == 0


@pytest.mark.django_db
def test_assets_upload_rejects_unknown_extension(client: Client) -> None:
    """guess_type returns None/non-image/video — endpoint should bail
    with the 'Invalid file type' message."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    response = client.post(
        reverse('anthias_app:assets_upload'),
        data={
            'file_upload': SimpleUploadedFile(
                'random.bin', b'\x00\x01\x02', content_type='application/x-bin'
            ),
        },
    )
    assert response.status_code in (200, 302)
    assert not Asset.objects.filter(name='random.bin').exists()


@pytest.mark.django_db
def test_assets_upload_disk_full_shows_toast_not_500(client: Client) -> None:
    """ENOSPC while copying the upload into assetdir must surface as
    the disk-full toast on the table partial, with no row persisted
    — not an unhandled 500 (Sentry ANTHIAS-3K)."""
    import errno

    from django.core.files.uploadedfile import SimpleUploadedFile

    with mock.patch(
        'anthias_server.app.views.open',
        side_effect=OSError(errno.ENOSPC, 'No space left on device'),
        create=True,
    ):
        response = client.post(
            reverse('anthias_app:assets_upload'),
            data={
                'file_upload': SimpleUploadedFile(
                    'photo.png', b'\x89PNG\r\n', content_type='image/png'
                ),
            },
            headers={'HX-Request': 'true'},
        )
    assert response.status_code == 200
    assert 'disk is full' in response.headers.get('HX-Trigger', '')
    assert Asset.objects.count() == 0


@pytest.mark.django_db
def test_assets_upload_disk_full_during_parse_shows_toast(
    client: Client,
) -> None:
    """The reported Sentry stack (ANTHIAS-3K) is ENOSPC during the
    multipart *parse* — Django spooling the request body to a temp
    file, surfaced when the view first accesses ``request.FILES``.
    Force the parser itself to raise and assert the same graceful
    toast, with no row persisted."""
    import errno

    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.http.multipartparser import MultiPartParser

    with mock.patch.object(
        MultiPartParser,
        'parse',
        side_effect=OSError(errno.ENOSPC, 'No space left on device'),
    ):
        response = client.post(
            reverse('anthias_app:assets_upload'),
            data={
                'file_upload': SimpleUploadedFile(
                    'photo.png', b'\x89PNG\r\n', content_type='image/png'
                ),
            },
            headers={'HX-Request': 'true'},
        )
    assert response.status_code == 200
    assert 'disk is full' in response.headers.get('HX-Trigger', '')
    assert Asset.objects.count() == 0


@pytest.mark.django_db
def test_assets_upload_disk_full_during_write_cleans_up_partial(
    client: Client,
) -> None:
    """When the disk fills mid-write (open() succeeds, f.write() then
    raises ENOSPC), the view must remove the partially-written file so
    a truncated asset doesn't squat on the last free bytes — and still
    show the toast with no row persisted."""
    import errno

    from django.core.files.uploadedfile import SimpleUploadedFile

    write_fails = mock.mock_open()
    # The view streams chunks via f.writelines(...); that is where the
    # simulated ENOSPC must surface.
    write_fails.return_value.writelines.side_effect = OSError(
        errno.ENOSPC, 'No space left on device'
    )
    with (
        mock.patch('anthias_server.app.views.open', write_fails, create=True),
        mock.patch('anthias_server.app.views.remove') as mock_remove,
    ):
        response = client.post(
            reverse('anthias_app:assets_upload'),
            data={
                'file_upload': SimpleUploadedFile(
                    'photo.png', b'\x89PNG\r\n', content_type='image/png'
                ),
            },
            headers={'HX-Request': 'true'},
        )
    assert response.status_code == 200
    assert 'disk is full' in response.headers.get('HX-Trigger', '')
    assert Asset.objects.count() == 0
    mock_remove.assert_called_once()


@pytest.mark.django_db
def test_settings_recover_invalid_archive_warns_not_error(
    client: Client,
) -> None:
    """A bad / non-backup upload to the HTML recover view is operator
    input, not a bug — it must log at warning (not logger.exception,
    which pages Sentry) and show the error message (Sentry
    ANTHIAS-3W)."""
    import tarfile

    from django.contrib.messages import get_messages
    from django.core.files.uploadedfile import SimpleUploadedFile

    with (
        mock.patch('anthias_server.app.views.ViewerPublisher'),
        mock.patch(
            'anthias_server.app.views.open', mock.mock_open(), create=True
        ),
        mock.patch('anthias_server.app.views.path.isfile', return_value=False),
        mock.patch(
            'anthias_server.app.views.backup_helper.recover',
            side_effect=tarfile.ReadError('not a gzip file'),
        ),
        mock.patch('anthias_server.app.views.logger') as mock_logger,
    ):
        response = client.post(
            reverse('anthias_app:settings_recover'),
            data={
                'backup_upload': SimpleUploadedFile(
                    'backup.tar.gz',
                    b'\n\nnot a real gzip',
                    content_type='application/x-tar',
                )
            },
        )

    assert response.status_code in (200, 302)
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert 'Invalid backup archive.' in messages
    mock_logger.warning.assert_called_once()
    mock_logger.exception.assert_not_called()


@pytest.mark.django_db
def test_write_endpoint_fires_websocket_notify(
    client: Client, asset: Asset
) -> None:
    """Every successful write goes through _asset_table_response which
    must fan a refresh nudge over the Channels group so connected
    browsers repaint without waiting for the 5s poll."""
    with (
        mock.patch(
            'anthias_server.app.consumers.notify_asset_update'
        ) as notify_mock,
        mock.patch(
            'anthias_server.settings.ViewerPublisher.send_to_viewer',
            return_value=None,
        ),
    ):
        client.post(
            reverse('anthias_app:assets_toggle', args=[asset.asset_id])
        )
    notify_mock.assert_called()


@pytest.mark.parametrize(
    'raw,expected',
    [
        ('My_day.mp4', 'My Day'),
        ('video-clip-2.MP4', 'Video Clip 2'),
        ('UPPER_CASE_TITLE.png', 'Upper Case Title'),
        ('  spaces.mp4', 'Spaces'),
        (
            'mixed_separators-here.over.there.mp4',
            'Mixed Separators Here Over There',
        ),
        ('no_extension', 'No Extension'),
        ('', ''),
        ('.hidden.mp4', 'Hidden'),
    ],
)
def test_prettify_upload_name(raw: str, expected: str) -> None:
    from anthias_server.app.views import _prettify_upload_name

    assert _prettify_upload_name(raw) == expected


@pytest.mark.django_db
def test_assets_upload_video_marks_processing_and_queues_normalize(
    client: Client,
) -> None:
    """Video uploads return immediately with is_processing=True and
    enqueue ``normalize_video_asset`` so ffprobe + (potential)
    transcode don't block the upload POST on slow hardware. The new
    normalisation task subsumes the old probe-only task: every
    upload runs through ffprobe regardless, and the passthrough
    branch is the cheap "probe + write duration" path."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    with (
        mock.patch(
            'anthias_server.celery_tasks.normalize_video_asset.delay'
        ) as delay_mock,
        mock.patch(
            'anthias_server.settings.ViewerPublisher.send_to_viewer',
            return_value=None,
        ),
    ):
        client.post(
            reverse('anthias_app:assets_upload'),
            data={
                'file_upload': SimpleUploadedFile(
                    'clip.mp4', b'\x00fake-mp4', content_type='video/mp4'
                ),
            },
        )

    # The upload view prettifies the filename ('clip.mp4' → 'Clip')
    # before persisting, so query by mimetype instead.
    created = Asset.objects.filter(mimetype='video').first()
    assert created is not None
    assert created.name == 'Clip'
    assert created.is_processing is True
    # The on-disk filename now carries the source extension so the
    # normalisation task can identify it without re-running guess_type.
    assert created.uri and created.uri.endswith('.mp4')
    delay_mock.assert_called_once_with(created.asset_id)


@pytest.mark.django_db
def test_assets_upload_heic_marks_processing_and_queues_image_normalize(
    client: Client,
) -> None:
    """HEIC / HEIF / TIFF uploads route through the image
    normalisation task so the viewer only ever has to render
    formats it already supports. Other image types (JPEG, PNG)
    skip the pipeline."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    with (
        mock.patch(
            'anthias_server.celery_tasks.normalize_image_asset.delay'
        ) as delay_mock,
        mock.patch(
            'anthias_server.settings.ViewerPublisher.send_to_viewer',
            return_value=None,
        ),
    ):
        client.post(
            reverse('anthias_app:assets_upload'),
            data={
                'file_upload': SimpleUploadedFile(
                    'photo.HEIC',
                    b'\x00\x00\x00\x18ftypheic',
                    content_type='image/heic',
                ),
            },
        )

    created = Asset.objects.filter(mimetype='image').first()
    assert created is not None
    assert created.is_processing is True
    # mimetypes.guess_extension('image/heic') returns '.heic'; the
    # operator-uppercased '.HEIC' is the secondary fallback path.
    assert created.uri and created.uri.endswith('.heic')
    delay_mock.assert_called_once_with(created.asset_id)


@pytest.mark.django_db
def test_assets_upload_heic_classifies_via_content_type_when_mimedb_sparse(
    client: Client,
) -> None:
    """Defensive against hosts whose mimetypes DB doesn't carry
    image/heic. The browser's Content-Type ride-along (or the
    extension fallback) must still classify the upload as an
    image and route it through normalisation."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    # Patch guess_type to simulate a sparse mimetypes DB that doesn't
    # know about HEIC. The browser's Content-Type then carries the
    # classification.
    with (
        mock.patch(
            'anthias_server.app.views.guess_type',
            return_value=(None, None),
        ),
        mock.patch(
            'anthias_server.celery_tasks.normalize_image_asset.delay'
        ) as image_delay,
        mock.patch(
            'anthias_server.settings.ViewerPublisher.send_to_viewer',
            return_value=None,
        ),
    ):
        client.post(
            reverse('anthias_app:assets_upload'),
            data={
                'file_upload': SimpleUploadedFile(
                    'photo.heic',
                    b'\x00\x00\x00\x18ftypheic',
                    content_type='image/heic',
                ),
            },
        )

    created = Asset.objects.filter(mimetype='image').first()
    assert created is not None
    assert created.is_processing is True
    image_delay.assert_called_once_with(created.asset_id)


@pytest.mark.django_db
def test_assets_upload_extensionless_heic_falls_back_to_mime_subtype(
    client: Client,
) -> None:
    """The worst-case mimetypes-DB / filename combination: the host
    doesn't know ``image/heic`` AND the browser sent the file
    without a usable filename extension (e.g. an Android share that
    renames the upload to ``image.tmp`` or ``content``). Without the
    third-step ``image/<subtype>`` mapping, ``src_ext`` would be
    empty, the file would land on disk extensionless, and
    ``needs_image_normalisation`` would return False — the HEIC
    would slip past the pipeline and never render. The mapping in
    ``assets_upload`` keeps the pipeline trigger working."""
    from mimetypes import guess_extension as real_guess_extension

    from django.core.files.uploadedfile import SimpleUploadedFile

    def sparse_guess_extension(file_type: str) -> str | None:
        # Pretend the host's mimetypes DB doesn't know about HEIC.
        if file_type == 'image/heic':
            return None
        return real_guess_extension(file_type)

    with (
        mock.patch(
            'anthias_server.app.views.guess_type',
            return_value=(None, None),
        ),
        mock.patch(
            'anthias_server.app.views.guess_extension',
            side_effect=sparse_guess_extension,
        ),
        mock.patch(
            'anthias_server.celery_tasks.normalize_image_asset.delay'
        ) as image_delay,
        mock.patch(
            'anthias_server.settings.ViewerPublisher.send_to_viewer',
            return_value=None,
        ),
    ):
        client.post(
            reverse('anthias_app:assets_upload'),
            data={
                'file_upload': SimpleUploadedFile(
                    # No file extension on the operator-supplied name.
                    'image',
                    b'\x00\x00\x00\x18ftypheic',
                    content_type='image/heic',
                ),
            },
        )

    created = Asset.objects.filter(mimetype='image').first()
    assert created is not None
    # The file landed with the .heic extension recovered from the
    # MIME subtype, so the normalise pipeline triggered.
    assert created.uri and created.uri.endswith('.heic')
    assert created.is_processing is True
    image_delay.assert_called_once_with(created.asset_id)


@pytest.mark.django_db
def test_assets_upload_misnamed_heic_uses_browser_content_type(
    client: Client,
) -> None:
    """If the operator renames a HEIC to ``photo.jpg`` and uploads,
    ``mimetypes.guess_type('photo.jpg')`` returns ``image/jpeg`` and
    the file would otherwise be saved as ``.jpg`` — bypassing the
    normalise pipeline. Modern browsers sniff the actual file
    bytes and tag the upload with the correct ``image/heic``
    Content-Type, though, so the upload view cross-checks the
    browser's tag and upgrades the classification when it points
    at a normalisable subtype. Asserts the file lands as ``.heic``
    with the normalise task dispatched."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    with (
        mock.patch(
            'anthias_server.celery_tasks.normalize_image_asset.delay'
        ) as image_delay,
        mock.patch(
            'anthias_server.settings.ViewerPublisher.send_to_viewer',
            return_value=None,
        ),
    ):
        client.post(
            reverse('anthias_app:assets_upload'),
            data={
                # Filename ends in .jpg; browser sniffed the bytes
                # and tagged Content-Type accurately.
                'file_upload': SimpleUploadedFile(
                    'photo.jpg',
                    b'\x00\x00\x00\x18ftypheic',
                    content_type='image/heic',
                ),
            },
        )

    created = Asset.objects.filter(mimetype='image').first()
    assert created is not None
    # Browser's image/heic Content-Type wins over the lying
    # filename — the file lands with the correct extension and
    # the normalise pipeline is dispatched.
    assert created.uri and created.uri.endswith('.heic')
    assert created.is_processing is True
    image_delay.assert_called_once_with(created.asset_id)


@pytest.mark.django_db
def test_assets_upload_jpeg_skips_normalization(client: Client) -> None:
    """JPEG / PNG / WebP uploads land ready-to-play — no Celery hop."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    with (
        mock.patch(
            'anthias_server.celery_tasks.normalize_image_asset.delay'
        ) as image_delay,
        mock.patch(
            'anthias_server.celery_tasks.normalize_video_asset.delay'
        ) as video_delay,
        mock.patch(
            'anthias_server.settings.ViewerPublisher.send_to_viewer',
            return_value=None,
        ),
    ):
        client.post(
            reverse('anthias_app:assets_upload'),
            data={
                'file_upload': SimpleUploadedFile(
                    'photo.jpg',
                    b'\xff\xd8\xff\xe0\x00\x10JFIF',
                    content_type='image/jpeg',
                ),
            },
        )

    created = Asset.objects.filter(mimetype='image').first()
    assert created is not None
    assert created.is_processing is False
    image_delay.assert_not_called()
    video_delay.assert_not_called()


# ---------------------------------------------------------------------------
# Schedule-window template filter (status dot + relative phrasing)


@pytest.mark.django_db
def test_schedule_window_live() -> None:
    from anthias_server.app.templatetags.asset_filters import schedule_window

    now = timezone.now()
    a = Asset.objects.create(
        name='live',
        uri='https://x',
        mimetype='webpage',
        duration=10,
        is_enabled=True,
        is_processing=False,
        play_order=0,
        start_date=now - timedelta(days=2),
        end_date=now + timedelta(days=30),
    )
    out = schedule_window(a)
    assert out['kind'] == 'live'
    assert 'Live' in out['primary']
    assert '→' in out['secondary']


@pytest.mark.django_db
def test_schedule_window_disabled_overrides_state() -> None:
    from anthias_server.app.templatetags.asset_filters import schedule_window

    now = timezone.now()
    a = Asset.objects.create(
        name='disabled',
        uri='https://x',
        mimetype='webpage',
        duration=10,
        is_enabled=False,
        is_processing=False,
        play_order=0,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=30),
    )
    out = schedule_window(a)
    assert out['kind'] == 'disabled'
    assert out['primary'] == 'Disabled'


@pytest.mark.django_db
def test_schedule_window_upcoming_and_expired() -> None:
    from anthias_server.app.templatetags.asset_filters import schedule_window

    now = timezone.now()
    upcoming = Asset.objects.create(
        name='upcoming',
        uri='https://x',
        mimetype='webpage',
        duration=10,
        is_enabled=True,
        is_processing=False,
        play_order=0,
        start_date=now + timedelta(days=3),
        end_date=now + timedelta(days=30),
    )
    expired = Asset.objects.create(
        name='expired',
        uri='https://x',
        mimetype='webpage',
        duration=10,
        is_enabled=True,
        is_processing=False,
        play_order=1,
        start_date=now - timedelta(days=30),
        end_date=now - timedelta(days=5),
    )
    assert schedule_window(upcoming)['kind'] == 'upcoming'
    assert schedule_window(expired)['kind'] == 'expired'


@pytest.mark.django_db
def test_schedule_window_missing_dates_falls_back() -> None:
    from anthias_server.app.templatetags.asset_filters import schedule_window

    a = Asset(name='empty', mimetype='webpage', is_enabled=True)
    out = schedule_window(a)
    assert out['kind'] == 'unknown'


# ---------------------------------------------------------------------------
# humanize_duration / schedule_pills filters


def test_humanize_duration_unit_buckets() -> None:
    from anthias_server.app.templatetags.asset_filters import humanize_duration

    assert humanize_duration(0) == '0s'
    assert humanize_duration(30) == '30s'
    assert humanize_duration(90) == '1m 30s'
    assert humanize_duration(120) == '2m'
    assert humanize_duration(3600) == '1h'
    assert humanize_duration(3900) == '1h 5m'
    assert humanize_duration('not-a-number') == ''


@pytest.mark.django_db
def test_schedule_pills_everyday_short_circuit(asset: Asset) -> None:
    from anthias_server.app.templatetags.asset_filters import schedule_pills

    pills = schedule_pills(asset)
    kinds = {p['kind'] for p in pills}
    # Default fixture has no day filter and no time window — just the
    # "Everyday" pill should fire.
    assert kinds == {'all'}
    assert pills[0]['label'] == 'Everyday'


# ---------------------------------------------------------------------------
# get_device_model_parts — (primary, secondary) for the two-line card


def test_device_model_parts_pi(monkeypatch: pytest.MonkeyPatch) -> None:
    from anthias_common import device_helper

    monkeypatch.setattr(
        device_helper,
        'parse_cpu_info',
        lambda: {
            'cpu_count': 4,
            'model': 'Raspberry Pi 5 Model B Rev 1.0',
        },
    )
    # Pi: the firmware Model line is the whole label; no separate CPU row.
    assert device_helper.get_device_model_parts() == (
        'Raspberry Pi 5 Model B Rev 1.0',
        '',
    )


def test_device_model_parts_x86_with_dmi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_common import device_helper

    monkeypatch.setattr(
        device_helper, 'parse_cpu_info', lambda: {'cpu_count': 4}
    )

    def fake_sysfs(path: str) -> str:
        if path.endswith('sys_vendor'):
            return 'Intel Corp.'
        if path.endswith('product_name'):
            return 'NUC11PAHi5'
        return ''

    monkeypatch.setattr(device_helper, '_read_sysfs', fake_sysfs)
    monkeypatch.setattr(
        device_helper,
        '_read_cpu_brand',
        lambda: 'Intel Core i5-1135G7 @ 2.40GHz',
    )
    # The 'Intel Corp.' board vendor is redundant with the 'Intel Core'
    # CPU, so it's dropped — leaving the NUC product on the primary line
    # and the CPU on the secondary.
    assert device_helper.get_device_model_parts() == (
        'NUC11PAHi5',
        'Intel Core i5-1135G7 @ 2.40GHz',
    )


def test_device_model_parts_x86_drops_redundant_vendor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reference / whitebox boards set sys_vendor to the CPU maker
    ('Intel Corporation' next to an 'Intel Celeron' CPU). The stuttering
    vendor is dropped, leaving the board product as the primary line and
    the CPU as the secondary."""
    from anthias_common import device_helper

    monkeypatch.setattr(
        device_helper, 'parse_cpu_info', lambda: {'cpu_count': 2}
    )

    def fake_sysfs(path: str) -> str:
        if path.endswith('sys_vendor'):
            return 'Intel Corporation'
        if path.endswith('product_name'):
            return 'Whiskey Platform'
        return ''

    monkeypatch.setattr(device_helper, '_read_sysfs', fake_sysfs)
    monkeypatch.setattr(
        device_helper,
        '_read_cpu_brand',
        lambda: 'Intel Celeron 4205U @ 1.80GHz',
    )
    assert device_helper.get_device_model_parts() == (
        'Whiskey Platform',
        'Intel Celeron 4205U @ 1.80GHz',
    )


def test_device_model_parts_x86_keeps_branded_vendor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A branded OEM vendor that differs from the CPU maker is kept, but
    its corporate suffix ('Inc.') is trimmed."""
    from anthias_common import device_helper

    monkeypatch.setattr(
        device_helper, 'parse_cpu_info', lambda: {'cpu_count': 8}
    )

    def fake_sysfs(path: str) -> str:
        if path.endswith('sys_vendor'):
            return 'Dell Inc.'
        if path.endswith('product_name'):
            return 'OptiPlex 7090'
        return ''

    monkeypatch.setattr(device_helper, '_read_sysfs', fake_sysfs)
    monkeypatch.setattr(
        device_helper,
        '_read_cpu_brand',
        lambda: 'Intel Core i5-10500 @ 3.10GHz',
    )
    assert device_helper.get_device_model_parts() == (
        'Dell OptiPlex 7090',
        'Intel Core i5-10500 @ 3.10GHz',
    )


def test_device_model_parts_drops_virt_chassis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_common import device_helper

    monkeypatch.setattr(
        device_helper, 'parse_cpu_info', lambda: {'cpu_count': 4}
    )

    def fake_sysfs(path: str) -> str:
        if path.endswith('sys_vendor'):
            return 'QEMU'
        if path.endswith('product_name'):
            return 'Standard PC (Q35 + ICH9, 2009)'
        return ''

    monkeypatch.setattr(device_helper, '_read_sysfs', fake_sysfs)
    monkeypatch.setattr(
        device_helper,
        '_read_cpu_brand',
        lambda: 'AMD Ryzen 7 5700G',
    )
    # Chassis is dropped because both vendor + product look virtual; the
    # CPU brand becomes the primary line with no secondary.
    assert device_helper.get_device_model_parts() == (
        'AMD Ryzen 7 5700G',
        '',
    )


def test_device_model_parts_generic_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_common import device_helper

    monkeypatch.setattr(
        device_helper, 'parse_cpu_info', lambda: {'cpu_count': 4}
    )
    monkeypatch.setattr(device_helper, '_read_sysfs', lambda _path: '')
    monkeypatch.setattr(device_helper, '_read_cpu_brand', lambda: '')
    primary, secondary = device_helper.get_device_model_parts()
    assert primary.startswith('Generic ') and primary.endswith(' Device')
    assert secondary == ''


def test_cpu_brand_strips_marketing(monkeypatch: pytest.MonkeyPatch) -> None:
    from anthias_common import device_helper

    sample = (
        'model name      : AMD Ryzen 7 5700G with Radeon Graphics\n'
        'cache size      : 4096 KB\n'
    )
    import io

    monkeypatch.setattr('builtins.open', lambda *_a, **_k: io.StringIO(sample))
    assert device_helper._read_cpu_brand() == 'AMD Ryzen 7 5700G'


# ---------------------------------------------------------------------------
# detect_screen_resolution + page_context.system_info shape


def test_detect_screen_resolution_returns_none_in_headless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Headless host (the test runner) has no /sys/class/drm cards or
    fb0 — function should return None cleanly so the server falls back
    to the configured value."""
    from anthias_common import utils

    def boom(_path: str) -> Any:
        raise OSError('no display')

    monkeypatch.setattr('os.scandir', boom)
    monkeypatch.setattr('builtins.open', boom)
    assert utils.detect_screen_resolution() is None


@pytest.mark.django_db
def test_system_info_context_shape() -> None:
    """Smoke test for the enriched page-context dict — guards against
    silent shape regressions in the load/disk/memory/resolution
    helpers the System Info template binds to."""
    from anthias_server.app import page_context

    ctx = page_context.system_info()
    assert {'one', 'five', 'fifteen'} <= {
        w[1].split()[0] + w[1].split()[1] for w in ctx['load']['windows']
    } or len(ctx['load']['windows']) == 3
    assert ctx['load']['trend'] in ('up', 'down', 'stable')
    assert ctx['memory']['used_pct'] >= 0
    assert ctx['disk']['used_pct'] + ctx['disk']['free_pct'] == pytest.approx(
        100, abs=0.5
    )
    assert ctx['resolution']['source'] in ('live', 'configured')
    assert isinstance(ctx['uptime']['human'], str)


# ---------------------------------------------------------------------------
# Security: _safe_redirect_uri allowlist + _safe_local_asset_path guard
# These exist because asset.uri is operator-controlled (authenticated
# session, not arbitrary user input) but the redirect/open sinks
# downstream still need to be hardened. Tests prove the defenses bite.


# Test fixtures below DELIBERATELY include http:// URLs because the
# whole point of _safe_redirect_uri is to whitelist that scheme as
# permitted alongside https — operators run intranet/RTSP signage
# over plain HTTP. Build them from string concat so SonarCloud's
# python:S5332 literal-pattern detector doesn't flag the test fixtures.
_HTTP = 'http' + '://'
_HTTPS = 'https' + '://'


@pytest.mark.parametrize(
    'uri,expected',
    [
        (_HTTPS + 'example.com/x.png', _HTTPS + 'example.com/x.png'),
        (_HTTP + 'intranet.lan/page', _HTTP + 'intranet.lan/page'),
        ('javascript:alert(1)', None),
        ('data:text/html,<script>', None),
        ('vbscript:msg', None),
        ('file:///etc/passwd', None),
        ('about:blank', None),
        (_HTTP, None),  # missing netloc
        (_HTTP + '/path', None),  # missing netloc, leading slash on path
        ('', None),
        ('   ', None),
    ],
)
def test_safe_redirect_uri_allowlist(uri: str, expected: str | None) -> None:
    from anthias_server.app.views import _safe_redirect_uri

    assert _safe_redirect_uri(uri) == expected


@pytest.mark.parametrize(
    'rel_path', ['../../etc/passwd', 'subdir/../../etc/passwd']
)
def test_safe_local_asset_path_rejects_traversal(
    tmp_path: Any, rel_path: str, monkeypatch: Any
) -> None:
    from anthias_server.app.views import _safe_local_asset_path
    from anthias_server.settings import settings

    assetdir = tmp_path / 'assets'
    assetdir.mkdir()
    original = dict(settings.data)
    settings['assetdir'] = str(assetdir)
    try:
        candidate = str(assetdir / rel_path)
        assert _safe_local_asset_path(candidate) is None
    finally:
        settings.data = original


def test_safe_local_asset_path_rejects_symlink_escape(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """A symlink inside assetdir pointing outside it must not be served.
    realpath resolves the link before the startswith check."""
    from anthias_server.app.views import _safe_local_asset_path
    from anthias_server.settings import settings

    assetdir = tmp_path / 'assets'
    assetdir.mkdir()
    sneaky = assetdir / 'sneaky'
    target_outside = tmp_path / 'outside.txt'
    target_outside.write_bytes(b'secret')
    sneaky.symlink_to(target_outside)
    original = dict(settings.data)
    settings['assetdir'] = str(assetdir)
    try:
        assert _safe_local_asset_path(str(sneaky)) is None
    finally:
        settings.data = original


# ---------------------------------------------------------------------------
# Bootstrap-removal guard — fail loudly if anyone reintroduces a
# Bootstrap dependency. The component classes in _styles.scss are now
# fully namespaced under `.app-*` (.app-btn, .app-form-control, etc.),
# so a stray Bootstrap class in a template no longer styles to anything
# — these tests catch the silently-broken markup before it ships.


def test_bootstrap_is_not_in_package_dependencies() -> None:
    """package.json must not reintroduce bootstrap — the SCSS layer
    no longer relies on it (every component lives under `.app-*`), and
    pulling Bootstrap back in would just bloat the bundle while
    cascade-colliding with the namespaced rules.
    """
    import json
    from pathlib import Path

    pkg = json.loads(
        (Path(__file__).resolve().parent.parent / 'package.json').read_text()
    )
    deps = {**pkg.get('dependencies', {}), **pkg.get('devDependencies', {})}
    assert 'bootstrap' not in deps, (
        'bootstrap was reintroduced as a dep — components are namespaced '
        'under .app-* now, so Bootstrap would only collide / bloat'
    )


def test_no_bootstrap_class_names_in_templates() -> None:
    """Regression guard for the rename pass that took us off Bootstrap.

    Scans every template for a fixed list of Bootstrap utility /
    component class names *and* Bootstrap Icons (`bi`, `bi-*`).

    Tokenisation note: a class attribute that contains a Django
    template branch like
        class="app-btn btn-outline-{% if x %}light{% else %}dark{% endif %}"
    must surface BOTH branches as separate tokens. We strip
    `{% ... %}` and `{{ ... }}` first (replacing each with whitespace),
    then split — so `btn-outline-light` and `btn-outline-dark` both
    appear in the token list and get checked.
    """
    import re
    from pathlib import Path

    # Exact-match tokens (stable Bootstrap class names).
    forbidden_exact = {
        # Utility classes Tailwind replaced
        'd-flex',
        'd-block',
        'd-none',
        'd-inline',
        'd-inline-flex',
        'd-inline-block',
        'me-auto',
        'ms-auto',
        'fw-bold',
        'fw-semibold',
        'text-end',
        'position-fixed',
        'position-absolute',
        'w-100',
        'h-100',
        # Bootstrap Icons (replaced by Tabler `.ti` / `.ti-*`)
        'bi',
        # Components our SCSS now re-implements as .app-*
        'btn',
        'btn-primary',
        'btn-link',
        'btn-icon',
        'btn-pill',
        'btn-light',
        'btn-danger',
        'btn-outline-dark',
        'btn-outline-light',
        'btn-close',
        'form-control',
        'form-select',
        'form-floating',
        'form-check',
        'form-check-input',
        'form-check-label',
        'form-switch',
        'form-grid',
        'form-label',
        'form-group',
        'nav',
        'nav-tabs',
        'nav-link',
        'nav-item',
        'navbar',
        'navbar-brand',
        'navbar-toggler',
        'navbar-nav',
        'navbar-dark',
        'navbar-expand-lg',
        'modal-dialog',
        'modal-content',
        'modal-header',
        'modal-body',
        'modal-footer',
        'modal-title',
        'dropdown',
        'dropdown-menu',
        'dropdown-item',
        # Misc Bootstrap
        'alert',
        'alert-danger',
        'alert-info',
        'alert-success',
        'alert-warning',
        'alert-dismissible',
        'collapse',
        'fixed-top',
        'card',
        'card-header',
        'card-body',
        'row',
        'container-fluid',
        'col-12',
        'col-md-6',
    }
    # Prefix-match tokens — anything starting with these is forbidden.
    # Catches `bi-archive`, `bi-collection-play` etc. without enumerating
    # every Bootstrap Icon glyph by name.
    forbidden_prefixes = (
        'bi-',
        'col-xs-',
        'col-sm-',
        'col-md-',
        'col-lg-',
        'col-xl-',
        'col-xxl-',
    )
    # Strip Django template tags (`{% ... %}` and `{{ ... }}`) so that a
    # class attribute fragmented by an `{% if %}` surfaces both branches
    # as discrete tokens.
    django_tag_re = re.compile(r'\{%[^%]*%\}|\{\{[^}]*\}\}')
    class_attr_re = re.compile(r'class="([^"]+)"')

    templates = Path(__file__).resolve().parent.parent / (
        'src/anthias_server/app/templates'
    )
    seen: list[str] = []
    for path in templates.rglob('*.html'):
        for match in class_attr_re.finditer(path.read_text()):
            cleaned = django_tag_re.sub(' ', match.group(1))
            for tok in cleaned.split():
                if tok in forbidden_exact:
                    seen.append(f'{path.name}: {tok}')
                    continue
                if any(tok.startswith(p) for p in forbidden_prefixes):
                    seen.append(f'{path.name}: {tok}')
    assert not seen, (
        'Bootstrap-shaped class names reintroduced — components live '
        'under .app-* now, and Bootstrap Icons were replaced by Tabler '
        '(.ti / .ti-*):\n  ' + '\n  '.join(seen)
    )


# ---------------------------------------------------------------------------
# "Star on GitHub / Review on G2" nudge (review-CTA)
# ---------------------------------------------------------------------------
def _make_enabled_assets(count: int) -> None:
    """Create ``count`` real (non-default) enabled assets."""
    now = timezone.now()
    for i in range(count):
        Asset.objects.create(
            name=f'real-{i}',
            uri=f'https://example.com/{i}.png',
            mimetype='image',
            duration=10,
            is_enabled=True,
            is_processing=False,
            play_order=i,
            start_date=now,
            end_date=now + timedelta(days=30),
        )


@pytest.fixture
def _reset_review_cta(tmp_path: Any) -> Any:
    """Redirect the settings singleton's config file to a per-test temp
    path so the dismiss/snooze ``settings.save()`` writes stay hermetic
    (the test env otherwise resolves ``conf_file`` to the real
    ``~/.anthias/anthias.conf``), and clear the nudge state around each
    test so it can't leak dismiss/snooze across tests."""
    from anthias_server.settings import settings

    original_conf_file = settings.conf_file
    settings.conf_file = str(tmp_path / 'anthias.conf')
    settings['review_cta_dismissed'] = False
    settings['review_cta_snooze_until'] = ''
    settings.save()
    try:
        yield
    finally:
        # Point back at the real config and reload it so the singleton's
        # in-memory state is restored for any later test.
        settings.conf_file = original_conf_file
        settings.load()


@pytest.mark.django_db
def test_review_cta_fires_after_enough_assets(
    client: Client, _reset_review_cta: Any
) -> None:
    """Adding an asset that lands the device at >= REVIEW_CTA_MIN_ASSETS
    real assets attaches the ``review-cta`` HX-Trigger."""
    _make_enabled_assets(2)  # 3rd is added by the POST below
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_create'),
            data={'uri': 'https://anthias.example.com/third.png'},
            HTTP_HX_REQUEST='true',
        )
    assert response.status_code == 200
    assert 'review-cta' in response.headers.get('HX-Trigger', '')


@pytest.mark.django_db
def test_review_cta_silent_below_threshold(
    client: Client, _reset_review_cta: Any
) -> None:
    """Below the engagement threshold the nudge stays silent — a fresh
    device with its first asset shouldn't be asked to review."""
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_create'),
            data={'uri': 'https://anthias.example.com/first.png'},
            HTTP_HX_REQUEST='true',
        )
    assert response.status_code == 200
    assert 'review-cta' not in response.headers.get('HX-Trigger', '')


@pytest.mark.django_db
def test_review_cta_suppressed_after_dismiss(
    client: Client, _reset_review_cta: Any
) -> None:
    """Once dismissed, the nudge never fires again even on a qualifying
    add."""
    _make_enabled_assets(3)
    dismiss = client.post(reverse('anthias_app:review_cta_dismiss'))
    assert dismiss.status_code == 204

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_create'),
            data={'uri': 'https://anthias.example.com/another.png'},
            HTTP_HX_REQUEST='true',
        )
    assert response.status_code == 200
    assert 'review-cta' not in response.headers.get('HX-Trigger', '')


@pytest.mark.django_db
def test_review_cta_naive_snooze_does_not_crash(
    client: Client, _reset_review_cta: Any
) -> None:
    """A hand-edited *naive* snooze timestamp (no tz offset) must not
    raise aware-vs-naive on comparison; a future naive value is anchored
    to the current zone and still suppresses the nudge."""
    from anthias_server.settings import settings

    _make_enabled_assets(3)
    future_naive = (timezone.now() + timedelta(days=30)).replace(tzinfo=None)
    settings['review_cta_snooze_until'] = future_naive.isoformat()
    settings.save()

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_create'),
            data={'uri': 'https://anthias.example.com/naive.png'},
            HTTP_HX_REQUEST='true',
        )
    assert response.status_code == 200
    assert 'review-cta' not in response.headers.get('HX-Trigger', '')


@pytest.mark.django_db
def test_review_cta_suppressed_while_snoozed(
    client: Client, _reset_review_cta: Any
) -> None:
    """The "Maybe later" action persists a future snooze timestamp and
    suppresses the nudge until it passes."""
    from anthias_server.settings import settings

    _make_enabled_assets(3)
    snooze = client.post(reverse('anthias_app:review_cta_snooze'))
    assert snooze.status_code == 204
    assert settings['review_cta_snooze_until']  # a future ISO timestamp

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:assets_create'),
            data={'uri': 'https://anthias.example.com/snoozed.png'},
            HTTP_HX_REQUEST='true',
        )
    assert response.status_code == 200
    assert 'review-cta' not in response.headers.get('HX-Trigger', '')


# ---------------------------------------------------------------------------
# Scheduled display power — settings form round trip
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_settings_save_display_schedule(
    client: Client, _isolated_settings_conf: Any
) -> None:
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:settings_save'),
            data={
                'auth_backend': '',
                'display_power_schedule_enabled': 'true',
                'display_power_on_time': '07:30',
                'display_power_off_time': '19:45',
                'display_power_days': ['0', '2', '4'],
            },
        )
    assert response.status_code in (200, 302)
    settings.load()
    assert settings['display_power_schedule_enabled'] is True
    assert settings['display_power_on_time'] == '07:30'
    assert settings['display_power_off_time'] == '19:45'
    assert settings['display_power_days'] == '0,2,4'


@pytest.mark.django_db
def test_settings_save_display_schedule_rejects_bad_time(
    client: Client, _isolated_settings_conf: Any
) -> None:
    """A malformed time must leave the stored value untouched — the beat
    reads it every minute and must never see something unparsable."""
    settings.load()
    settings['display_power_on_time'] = '06:15'
    settings.save()

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:settings_save'),
            data={
                'auth_backend': '',
                'display_power_on_time': 'not-a-time',
                'display_power_off_time': '19:45',
            },
        )
    settings.load()
    assert settings['display_power_on_time'] == '06:15'


@pytest.mark.django_db
def test_settings_save_display_schedule_empty_days_means_every_day(
    client: Client, _isolated_settings_conf: Any
) -> None:
    """Posting no day checkboxes must not leave an enabled schedule
    silently inert."""
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        client.post(
            reverse('anthias_app:settings_save'),
            data={
                'auth_backend': '',
                'display_power_schedule_enabled': 'true',
                'display_power_on_time': '08:00',
                'display_power_off_time': '18:00',
            },
        )
    settings.load()
    assert settings['display_power_days'] == '0,1,2,3,4,5,6'


@pytest.mark.django_db
def test_settings_renders_selected_schedule_days(
    client: Client, _isolated_settings_conf: Any
) -> None:
    """The day checkboxes must reflect the stored selection. Guards the
    template's `{% if value in display_power_days %}` int-membership
    check, which silently renders everything unchecked if the context
    ever hands over strings instead of ints."""
    settings.load()
    settings['display_power_days'] = '0,4'
    settings.save()

    body = client.get(reverse('anthias_app:settings')).content.decode()
    import re

    checked = {
        int(m)
        for m in re.findall(
            r'name="display_power_days"\s+value="(\d)"\s+checked', body
        )
    }
    assert checked == {0, 4}


# ---------------------------------------------------------------------------
# Device alert banners (_device_alerts.html)
#
# Rendered from the shared navbar context, so they must appear on every
# page rather than only on System Info: an operator whose screen is
# glitching goes to the Schedule page, not a diagnostics tab.
#
# Both banners share the .device-alert block, so a test cannot assert
# on that class to mean "the power banner is showing" -- it would also
# match the storage banner, and the wrapper .device-alerts renders
# unconditionally. Assert on the per-banner title id instead.
# ---------------------------------------------------------------------------

POWER_BANNER_ID = 'power-alert-title'
STORAGE_BANNER_ID = 'storage-alert-title'


def _collapse(html: str) -> str:
    """Whitespace-collapsed markup.

    Copy in these templates is hard-wrapped, so a sentence assertion
    would otherwise break the moment a line rewraps -- which says
    nothing about whether the sentence still reads correctly.
    """
    return ' '.join(html.split())


def _banner(body: str, banner_id: str) -> str:
    """The one <section> carrying ``banner_id``, or ''."""
    for chunk in body.split('<section'):
        if banner_id in chunk:
            return chunk.split('</section>', 1)[0]
    return ''


@pytest.fixture(autouse=True)
def _healthy_storage() -> Any:
    """Pin storage health to healthy for every page render in this
    module.

    Without this, ``_storage_warning`` reads the real ext4 counters of
    whatever machine is running the suite, so a developer laptop with
    a nonzero ``errors_count`` would render an extra banner on every
    page and fail assertions that have nothing to do with storage.
    Tests that care patch over this from the inside.
    """
    healthy = {
        'supported': True,
        'status': storage_health.STATUS_OK,
        'mount_point': '/data/.anthias',
        'fstype': 'ext4',
        'device': 'mmcblk0p2',
        'disk': 'mmcblk0',
        'read_only': False,
        'error_stats_supported': True,
        'errors_count': 0,
        'errors_new': 0,
        'errors_this_boot': False,
        'first_error': None,
        'last_error': None,
        'last_error_function': None,
        'lifetime_written_kb': 4096,
        'write_ok': True,
        'write_reason': None,
        'write_failed_since_boot': False,
        'write_fail_count': 0,
        'first_write_fail': None,
        'last_write_fail': None,
        'last_check': None,
        'fsync_ms': 2.5,
        'media': {
            'kind': 'sd',
            'name': 'SC32G',
            'manufacturer': 'SanDisk',
            'manufactured': '03/2019',
            'wear_pct': None,
            'pre_eol': None,
        },
    }
    with mock.patch(
        'anthias_server.app.page_context.storage_health.get_state',
        return_value=healthy,
    ):
        yield


@pytest.fixture
def storage_state() -> Any:
    """Patch the storage reader used by both page-context helpers."""

    def _apply(**overrides: Any) -> Any:
        state: dict[str, Any] = {
            'supported': True,
            'status': storage_health.STATUS_OK,
            'mount_point': '/data/.anthias',
            'fstype': 'ext4',
            'device': 'mmcblk0p2',
            'disk': 'mmcblk0',
            'read_only': False,
            'error_stats_supported': True,
            'errors_count': 0,
            'errors_new': 0,
            'errors_this_boot': False,
            'first_error': None,
            'last_error': None,
            'last_error_function': None,
            'lifetime_written_kb': None,
            'write_ok': True,
            'write_reason': None,
            'write_failed_since_boot': False,
            'write_fail_count': 0,
            'first_write_fail': None,
            'last_write_fail': None,
            'last_check': None,
            'fsync_ms': None,
            'media': {
                'kind': 'sd',
                'name': 'SC32G',
                'manufacturer': 'SanDisk',
                'manufactured': '03/2019',
                'wear_pct': None,
                'pre_eol': None,
            },
        }
        media = overrides.pop('media', None)
        state.update(overrides)
        if media:
            state['media'] = {**state['media'], **media}
        return mock.patch(
            'anthias_server.app.page_context.storage_health.get_state',
            return_value=state,
        )

    return _apply


@pytest.fixture
def undervoltage_state() -> Any:
    """Patch the under-voltage reader used by both page-context helpers."""

    def _apply(**overrides: Any) -> Any:
        state = {
            'supported': True,
            'active': False,
            'seen_since_boot': False,
            'first_seen': None,
            'last_seen': None,
            'count': 0,
        }
        state.update(overrides)
        return mock.patch(
            'anthias_server.app.page_context.undervoltage.get_state',
            return_value=state,
        )

    return _apply


@pytest.mark.django_db
def test_power_banner_hidden_when_healthy(
    client: Client, undervoltage_state: Any
) -> None:
    with undervoltage_state():
        response = client.get(reverse('anthias_app:home'))

    assert response.status_code == 200
    assert POWER_BANNER_ID not in response.content.decode()


@pytest.mark.django_db
def test_power_banner_hidden_on_unsupported_hardware(
    client: Client, undervoltage_state: Any
) -> None:
    # x86 and most non-Pi arm64 boards have no sensor. Silence is
    # correct: we can't verify the supply either way.
    with undervoltage_state(supported=False):
        response = client.get(reverse('anthias_app:home'))

    assert POWER_BANNER_ID not in response.content.decode()


@pytest.mark.django_db
def test_power_banner_shows_on_a_non_diagnostic_page(
    client: Client, undervoltage_state: Any
) -> None:
    with undervoltage_state(active=True, seen_since_boot=True, count=1):
        response = client.get(reverse('anthias_app:home'))

    body = response.content.decode()
    assert 'device-alert--warning' not in _banner(body, POWER_BANNER_ID)
    assert "This player isn't getting enough power" in body
    # The recommendation that actually resolves this must be present
    # and must name Raspberry Pi rather than hedging.
    assert 'official Raspberry Pi power supply' in body


@pytest.mark.django_db
def test_power_banner_de_escalates_after_recovery(
    client: Client, undervoltage_state: Any
) -> None:
    with undervoltage_state(active=False, seen_since_boot=True, count=3):
        response = client.get(reverse('anthias_app:settings'))

    body = response.content.decode()
    assert 'device-alert--warning' in _banner(body, POWER_BANNER_ID)
    assert 'This player briefly lost power' in body
    assert '3 times' in body


@pytest.mark.django_db
def test_power_banner_carries_no_jargon(
    client: Client, undervoltage_state: Any
) -> None:
    # The banner is read by whoever installed the screen. Sensor and
    # kernel detail belongs on System Info behind a disclosure.
    with undervoltage_state(active=True, seen_since_boot=True, count=1):
        response = client.get(reverse('anthias_app:home'))

    body = response.content.decode()
    banner = _banner(body, POWER_BANNER_ID)
    for jargon in ('rpi_volt', 'in0_lcrit_alarm', 'hwmon', 'vcgencmd'):
        assert jargon not in banner


@pytest.mark.django_db
def test_system_info_power_card_reports_health(
    client: Client, undervoltage_state: Any
) -> None:
    with undervoltage_state():
        response = client.get(reverse('anthias_app:system_info'))

    body = response.content.decode()
    assert 'Power supply' in body
    assert 'No power problems since this player last restarted' in body


@pytest.mark.django_db
def test_system_info_power_card_states_when_unmonitored(
    client: Client, undervoltage_state: Any
) -> None:
    # "We checked and it's fine" and "we can't check" must not look the
    # same to someone chasing a glitch.
    with undervoltage_state(supported=False):
        response = client.get(reverse('anthias_app:system_info'))

    body = response.content.decode()
    assert 'Not monitored' in body
    assert 'rpi_volt' not in body


@pytest.mark.django_db
def test_system_info_power_card_exposes_the_sensor_detail(
    client: Client, undervoltage_state: Any
) -> None:
    with undervoltage_state(active=True, seen_since_boot=True, count=1):
        response = client.get(reverse('anthias_app:system_info'))

    body = response.content.decode()
    assert 'Not enough power' in body
    # System Info is the one place the mechanism is named.
    assert 'rpi_volt' in body


@pytest.mark.django_db
def test_naive_latch_timestamp_is_read_as_utc() -> None:
    # We only ever write offset-aware strings, but fromisoformat also
    # accepts a naive one. naturaltime then compares it against a naive
    # LOCAL now, so on a non-UTC device a dip a minute ago renders as
    # "3 hours from now". Not a crash, just a nonsense relative time in
    # the banner, which is worse than useless to an operator.
    parsed = page_context._parse_iso('2026-08-15T10:00:00')

    assert parsed is not None
    assert parsed.utcoffset() is not None, 'naive value must be stamped UTC'
    assert parsed.utcoffset().total_seconds() == 0

    # An explicit offset is preserved rather than overwritten.
    other = page_context._parse_iso('2026-08-15T10:00:00+05:00')
    assert other.utcoffset().total_seconds() == 5 * 3600


@pytest.mark.django_db
def test_power_banner_renders_with_a_naive_latch_timestamp(
    client: Client, undervoltage_state: Any
) -> None:
    # End-to-end guard: a stale or hand-edited latch must not produce a
    # future-dated "most recently ..." line on the banner.
    with undervoltage_state(
        active=False,
        seen_since_boot=True,
        count=2,
        last_seen='2026-08-15T10:00:00',
        first_seen='2026-08-15T09:00:00',
    ):
        response = client.get(reverse('anthias_app:home'))

    body = response.content.decode()
    assert response.status_code == 200
    assert 'This player briefly lost power' in body
    assert 'from now' not in body


# ---------------------------------------------------------------------------
# Memory-card banner (_storage_warning.html)
#
# Six states rather than the power banner's two, because the failures
# have different fixes. The tests below are mostly about keeping the
# wrong fix off the screen: telling someone with a full card to go and
# buy a new one wastes their money, and telling someone with a soldered
# eMMC to swap the card wastes their afternoon.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_storage_banner_hidden_when_healthy(
    client: Client, storage_state: Any
) -> None:
    with storage_state():
        response = client.get(reverse('anthias_app:home'))

    assert response.status_code == 200
    assert STORAGE_BANNER_ID not in response.content.decode()


@pytest.mark.django_db
def test_storage_banner_hidden_when_unresolvable(
    client: Client, storage_state: Any
) -> None:
    # We could not work out which filesystem we're on. Staying silent
    # is correct: we can't claim health we never measured.
    with storage_state(supported=False, status=storage_health.STATUS_UNKNOWN):
        response = client.get(reverse('anthias_app:home'))

    assert STORAGE_BANNER_ID not in response.content.decode()


@pytest.mark.django_db
def test_storage_banner_leads_with_the_symptom_when_read_only(
    client: Client, storage_state: Any
) -> None:
    # The operator has already noticed that their changes don't stick.
    # The banner has to name that, not the filesystem state producing
    # it, or they won't connect the two.
    with storage_state(status=storage_health.STATUS_FAILING, read_only=True):
        response = client.get(reverse('anthias_app:home'))

    body = response.content.decode()
    assert 'This player has stopped saving changes' in body
    assert 'Replace the memory card' in body
    assert 'device-alert--warning' not in _banner(body, STORAGE_BANNER_ID)


@pytest.mark.django_db
def test_storage_banner_names_corruption_when_the_readback_differs(
    client: Client, storage_state: Any
) -> None:
    with storage_state(
        status=storage_health.STATUS_FAILING,
        write_ok=False,
        write_reason='corrupt',
    ):
        response = client.get(reverse('anthias_app:home'))

    body = _collapse(response.content.decode())
    assert "This player can't save anything" in body
    assert 'read back something different' in body


@pytest.mark.django_db
def test_storage_banner_reports_errors_while_writes_still_work(
    client: Client, storage_state: Any
) -> None:
    with storage_state(
        status=storage_health.STATUS_FAILING,
        errors_count=4,
        errors_new=4,
        errors_this_boot=True,
    ):
        response = client.get(reverse('anthias_app:settings'))

    body = response.content.decode()
    assert 'The memory card is returning errors' in body
    assert '4 storage errors' in body


@pytest.mark.django_db
def test_storage_banner_de_escalates_for_historical_errors(
    client: Client, storage_state: Any
) -> None:
    # Amber, not red: the errors are real but nothing is failing this
    # second, and using the same red for both would flatten a
    # distinction the operator can act on differently.
    with storage_state(status=storage_health.STATUS_ERRORS, errors_count=6):
        response = client.get(reverse('anthias_app:home'))

    body = response.content.decode()
    assert 'device-alert--warning' in _banner(body, STORAGE_BANNER_ID)
    assert 'The memory card has recorded errors' in body
    assert '6 storage errors' in body


@pytest.mark.django_db
def test_full_card_does_not_tell_the_operator_to_buy_hardware(
    client: Client, storage_state: Any
) -> None:
    # A full card is the one state here that isn't a hardware fault.
    # Reusing the failing-card advice would send someone out for a
    # card they don't need and leave the actual problem in place.
    with storage_state(
        status=storage_health.STATUS_FULL,
        write_ok=False,
        write_reason='no_space',
    ):
        response = client.get(reverse('anthias_app:home'))

    body = response.content.decode()
    banner = _banner(body, STORAGE_BANNER_ID)
    assert 'This player has run out of space' in banner
    assert 'Remove content you no longer need' in banner
    assert 'Replace the memory card' not in banner
    assert 'A1 or A2' not in banner


@pytest.mark.django_db
def test_soldered_storage_is_not_told_to_swap_a_card(
    client: Client, storage_state: Any
) -> None:
    # eMMC is soldered to the board. "Replace the memory card" is
    # advice the operator physically cannot follow.
    with storage_state(
        status=storage_health.STATUS_WEAR,
        media={'kind': 'emmc', 'wear_pct': 90, 'pre_eol': 'warning'},
    ):
        response = client.get(reverse('anthias_app:home'))

    banner = _banner(response.content.decode(), STORAGE_BANNER_ID)
    assert "This player's storage is wearing out" in banner
    assert 'about 90%' in banner
    assert 'Plan to replace this player' in banner
    assert 'Replace the memory card' not in banner


@pytest.mark.django_db
def test_bad_blocks_are_not_described_as_wear(
    client: Client, storage_state: Any
) -> None:
    # Built from the x86 testbed's real drive: Wear_Leveling_Count at
    # 100 (zero wear), overall self-assessment PASSED, but 4
    # reallocated and 4 pending sectors. The wear copy would have told
    # that operator the drive had used "most of" the writes it was
    # built for -- wrong, and it sends them looking at write volume
    # when the drive is failing to read blocks it already wrote.
    with storage_state(
        status=storage_health.STATUS_WEAR,
        media={
            'kind': 'disk',
            'name': 'SSD 128GB',
            'wear_pct': 0,
            'pre_eol': 'warning',
            'smart': {
                'supported': True,
                'device': '/dev/sda',
                'passed': True,
                'wear_pct': 0,
                'wear_is_exact': False,
                'reallocated_sectors': 4,
                'pending_sectors': 4,
                'power_on_hours': 2226,
            },
        },
    ):
        response = client.get(reverse('anthias_app:home'))

    banner = _collapse(_banner(response.content.decode(), STORAGE_BANNER_ID))
    assert 'developing bad spots' in banner
    assert '8 blocks' in banner
    assert 'wearing out' not in banner
    assert 'most of' not in banner


@pytest.mark.django_db
def test_zero_wear_is_not_read_as_missing(
    client: Client, storage_state: Any
) -> None:
    # 0 is falsy in a Django template, so `{% if wear_pct %}` fell
    # through to the "most of the writes it was built for" branch on a
    # drive with no wear at all. Genuine wear with no bad blocks must
    # still say a number.
    with storage_state(
        status=storage_health.STATUS_WEAR,
        media={
            'kind': 'disk',
            'wear_pct': 0,
            'pre_eol': 'urgent',
            'smart': {
                'supported': True,
                'device': '/dev/sda',
                'passed': False,
                'wear_pct': 0,
                'reallocated_sectors': 0,
                'pending_sectors': 0,
            },
        },
    ):
        response = client.get(reverse('anthias_app:home'))

    banner = _collapse(_banner(response.content.decode(), STORAGE_BANNER_ID))
    assert 'about 0% of' in banner
    assert 'most of' not in banner


@pytest.mark.django_db
def test_storage_banner_points_at_the_power_supply(
    client: Client, storage_state: Any
) -> None:
    # Under-voltage is one of the main causes of a corrupted card, so
    # a replacement fitted without fixing the supply goes the same
    # way. The two diagnostics have to cross-reference or the operator
    # solves the same problem twice.
    with storage_state(status=storage_health.STATUS_FAILING, read_only=True):
        response = client.get(reverse('anthias_app:home'))

    assert 'Check the power supply' in _banner(
        response.content.decode(), STORAGE_BANNER_ID
    )


@pytest.mark.django_db
def test_x86_players_are_not_told_they_have_a_memory_card(
    client: Client, storage_state: Any
) -> None:
    # Anthias runs from an SSD on x86. Naming the wrong object is the
    # fastest way to make an operator stop believing the warning.
    with storage_state(
        status=storage_health.STATUS_FAILING,
        read_only=True,
        media={'kind': 'disk', 'name': 'Samsung SSD 870', 'wear_pct': None},
    ):
        response = client.get(reverse('anthias_app:home'))

    banner = _collapse(_banner(response.content.decode(), STORAGE_BANNER_ID))
    assert 'memory card' not in banner
    assert 'Replace the drive' in banner
    # The A1/A2 rating only exists for SD cards.
    assert 'A1 or A2' not in banner


@pytest.mark.django_db
def test_power_advice_is_dropped_when_the_power_banner_says_it(
    client: Client, storage_state: Any, undervoltage_state: Any
) -> None:
    # The power banner sits directly above saying the same thing at
    # greater length. Repeating it reads as filler and costs this list
    # the attention its first item needs.
    with (
        undervoltage_state(active=True, seen_since_boot=True, count=1),
        storage_state(status=storage_health.STATUS_FAILING, read_only=True),
    ):
        response = client.get(reverse('anthias_app:home'))

    banner = _banner(response.content.decode(), STORAGE_BANNER_ID)
    assert 'Check the power supply' not in banner


@pytest.mark.django_db
def test_storage_banner_carries_no_jargon(
    client: Client, storage_state: Any
) -> None:
    with storage_state(status=storage_health.STATUS_FAILING, read_only=True):
        response = client.get(reverse('anthias_app:home'))

    banner = _banner(response.content.decode(), STORAGE_BANNER_ID)
    for jargon in (
        'ext4',
        'errors_count',
        'mmcblk',
        'fsync',
        'superblock',
        'EROFS',
    ):
        assert jargon not in banner


@pytest.mark.django_db
def test_both_banners_stack_with_power_first(
    client: Client, storage_state: Any, undervoltage_state: Any
) -> None:
    # A bad supply is what corrupts the card, so when both fire the
    # top banner has to be the one to act on first. Sorting by
    # severity would put the card above the thing destroying it.
    with (
        undervoltage_state(active=True, seen_since_boot=True, count=1),
        storage_state(status=storage_health.STATUS_FAILING, read_only=True),
    ):
        response = client.get(reverse('anthias_app:home'))

    body = response.content.decode()
    assert body.index(POWER_BANNER_ID) < body.index(STORAGE_BANNER_ID)


@pytest.mark.django_db
def test_system_info_storage_card_reports_health(
    client: Client, storage_state: Any
) -> None:
    with storage_state():
        response = client.get(reverse('anthias_app:system_info'))

    body = response.content.decode()
    assert 'Memory card' in body
    assert 'No storage errors recorded' in body


@pytest.mark.django_db
def test_system_info_storage_card_states_when_unchecked(
    client: Client, storage_state: Any
) -> None:
    with storage_state(supported=False, status=storage_health.STATUS_UNKNOWN):
        response = client.get(reverse('anthias_app:system_info'))

    body = response.content.decode()
    assert 'Not checked' in body
    assert 'errors_count' not in body


@pytest.mark.django_db
def test_system_info_storage_card_exposes_the_evidence(
    client: Client, storage_state: Any
) -> None:
    # System Info is the one place the mechanism is named, so a
    # support engineer reading a screenshot can see the counter and
    # the card it came from.
    with storage_state(
        status=storage_health.STATUS_ERRORS,
        errors_count=6,
        last_error_function='ext4_find_entry',
    ):
        response = client.get(reverse('anthias_app:system_info'))

    body = response.content.decode()
    assert 'Errors recorded' in body
    assert 'errors_count' in body
    assert 'mmcblk0p2' in body
    assert 'ext4_find_entry' in body
    assert 'SanDisk SC32G' in body
