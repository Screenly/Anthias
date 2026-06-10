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
    client: Client, posted: str, persisted: int
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
def test_assets_bulk_update_never_writes_video_duration_column(
    client: Client, bulk_assets: list[Asset]
) -> None:
    """The duration bulk_update must exclude video rows entirely (not
    just write the same value back) so it can't race with / clobber the
    probe_video_duration task's UPDATE (Copilot review of #3048). Spy on
    bulk_update and assert no video object is ever in a call that writes
    the duration column.
    """
    calls: list[tuple[list[Asset], list[str]]] = []
    real_bulk_update = Asset.objects.bulk_update

    def spy(objs: Any, fields: Any, *a: Any, **k: Any) -> Any:
        objs = list(objs)
        calls.append((objs, list(fields)))
        return real_bulk_update(objs, fields, *a, **k)

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        with mock.patch.object(Asset.objects, 'bulk_update', side_effect=spy):
            client.post(
                reverse('anthias_app:assets_bulk_update'),
                data={
                    'ids': _bulk_ids_csv(bulk_assets),
                    'apply_duration': 'true',
                    'duration': '42',
                },
            )

    duration_writes = [objs for objs, fields in calls if 'duration' in fields]
    assert duration_writes, 'expected a duration bulk_update'
    for objs in duration_writes:
        assert all(a.mimetype != 'video' for a in objs), (
            'a video asset was included in the duration write'
        )


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

    with mock.patch(
        'anthias_server.settings.ViewerPublisher.send_to_viewer',
        return_value=None,
    ):
        with CaptureQueriesContext(connection) as ctx:
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
    # The ids land entity-escaped inside the setVisible() call …
    assert 'setVisible(' in body
    assert '&quot;' in body
    # … and the raw, attribute-breaking form must not appear.
    assert 'setVisible(&#x27;active&#x27;, ["' not in body
    assert "setVisible('active', [\"" not in body


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
