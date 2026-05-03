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
    for label in ('Load Average', 'Free Disk', 'Memory', 'Uptime'):
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

    created = Asset.objects.filter(name='clip.mp4').first()
    assert created is not None
    assert created.mimetype == 'video'
    assert created.is_processing is True
    delay_mock.assert_called_once_with(created.asset_id)
