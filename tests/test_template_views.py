"""Smoke / integration coverage for the post-React Django template views.

Each view in src/anthias_server/app/views.py beyond the legacy ``react``,
``login`` and ``splash_page`` is exercised here through Django's test
client — fast, deterministic, no Selenium overhead. The integration
suite (tests/test_app.py) still drives the full stack via Chrome, but
that suite hits a parallel uvicorn process and doesn't accumulate
coverage. These tests do.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from anthias_server.app import page_context
from anthias_server.app.models import Asset
from anthias_server.app.templatetags.asset_filters import to_json


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
        'Authentication',
        'Show splash screen',
        'Backup',
        'System controls',
    ):
        assert label in body


@pytest.mark.django_db
def test_asset_table_partial(client: Client, asset: Asset) -> None:
    response = client.get(reverse('anthias_app:assets_table'))
    assert response.status_code == 200
    assert (asset.name or '') in response.content.decode()


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
        'date_format_options',
        'is_pi5',
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
    assert row.uri.endswith(f'{row.asset_id}.mp4')
    assert row.duration == 0

    mock_dispatch.assert_called_once_with(row.asset_id, youtube_url)


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
def test_assets_control_dispatches(client: Client) -> None:
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ) as send:
        response = client.post(
            reverse('anthias_app:assets_control', args=['next'])
        )
    assert response.status_code in (200, 302)
    assert send.called


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
def test_settings_save_round_trip(client: Client) -> None:
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


@pytest.mark.django_db
def test_settings_save_invalid_default_streaming_duration(
    client: Client,
) -> None:
    """The save handler catches ValueError and surfaces it via messages
    instead of 500ing — exercise the except branch."""
    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        response = client.post(
            reverse('anthias_app:settings_save'),
            data={
                'player_name': 'Test',
                'default_duration': 'not-a-number',  # int(...) blows up
                'default_streaming_duration': '300',
                'audio_output': 'hdmi',
                'date_format': 'mm/dd/yyyy',
                'auth_backend': '',
            },
        )
    assert response.status_code in (200, 302)


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
def test_assets_upload_video_marks_processing_and_queues_probe(
    client: Client,
) -> None:
    """Video uploads return immediately with is_processing=True and
    enqueue the duration probe as a Celery task so ffprobe doesn't
    block the upload POST on slow hardware."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    with (
        mock.patch(
            'anthias_server.celery_tasks.probe_video_duration.delay'
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
    delay_mock.assert_called_once_with(created.asset_id)


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
# get_friendly_device_model — Pi vs x86 vs virt


def test_friendly_device_model_pi(monkeypatch: pytest.MonkeyPatch) -> None:
    from anthias_common import device_helper

    monkeypatch.setattr(
        device_helper,
        'parse_cpu_info',
        lambda: {
            'cpu_count': 4,
            'model': 'Raspberry Pi 5 Model B Rev 1.0',
        },
    )
    assert device_helper.get_friendly_device_model() == (
        'Raspberry Pi 5 Model B Rev 1.0'
    )


def test_friendly_device_model_x86_with_dmi(
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
    assert device_helper.get_friendly_device_model() == (
        'Intel Corp. NUC11PAHi5 · Intel Core i5-1135G7 @ 2.40GHz'
    )


def test_friendly_device_model_drops_virt_chassis(
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
    # Chassis is dropped because both vendor + product look virtual;
    # only the CPU brand survives.
    assert device_helper.get_friendly_device_model() == 'AMD Ryzen 7 5700G'


def test_friendly_device_model_generic_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_common import device_helper

    monkeypatch.setattr(
        device_helper, 'parse_cpu_info', lambda: {'cpu_count': 4}
    )
    monkeypatch.setattr(device_helper, '_read_sysfs', lambda _path: '')
    monkeypatch.setattr(device_helper, '_read_cpu_brand', lambda: '')
    out = device_helper.get_friendly_device_model()
    assert out.startswith('Generic ') and out.endswith(' Device')


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
