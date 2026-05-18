"""
Tests for the viewer REST shim: GET /api/v2/viewer/playlist and
GET /api/v2/viewer/settings.

Both endpoints exist so the C++ viewer (GH #2906 Phase 3) can ask
the server for "what should I play right now and when do I need to
re-ask" without touching the Django ORM or reading the SQLite file
directly. They use the shared internal token derived from
anthias.conf — the same gating as POST /api/v2/assets/<id>/recheck.
"""

from collections.abc import Iterator
from datetime import time, timedelta
from typing import Any

import pytest
import time_machine
from django.test import Client
from django.utils import timezone

from anthias_common.internal_auth import (
    INTERNAL_AUTH_HEADER,
    internal_auth_token,
)
from anthias_server.app.models import Asset
from anthias_server.settings import settings as anthias_settings


_DEFAULT_PLAY_DAYS = '[1, 2, 3, 4, 5, 6, 7]'


@pytest.fixture(autouse=True)
def _internal_auth_secret() -> Iterator[None]:
    """Set a stable secret so internal_auth_token() resolves to a
    fixed value for the duration of the test, then restore."""
    original_secret = anthias_settings.get('django_secret_key', '')
    anthias_settings['django_secret_key'] = 'test-internal-secret'
    try:
        yield
    finally:
        anthias_settings['django_secret_key'] = original_secret


@pytest.fixture
def _restore_shuffle_setting() -> Iterator[None]:
    """Shuffle is a global setting — restore after each test that
    touches it so an early test can't leave the next one shuffled."""
    original = anthias_settings.get('shuffle_playlist', False)
    try:
        yield
    finally:
        anthias_settings['shuffle_playlist'] = original


def _auth_headers() -> dict[str, str]:
    return {INTERNAL_AUTH_HEADER: internal_auth_token(anthias_settings)}


def _make(**kwargs: Any) -> Asset:
    """Create an Asset with sensible defaults; override per test."""
    now = timezone.now()
    defaults: dict[str, Any] = {
        'mimetype': 'image',
        'name': 'a',
        'uri': 'http://example.com/x.png',
        'duration': 5,
        'is_enabled': True,
        'nocache': False,
        'is_processing': False,
        'play_order': 0,
        'skip_asset_check': False,
        'play_days': _DEFAULT_PLAY_DAYS,
        'play_time_from': None,
        'play_time_to': None,
        'is_reachable': True,
        'last_reachability_check': None,
        'metadata': {},
        'start_date': now - timedelta(days=1),
        'end_date': now + timedelta(days=1),
    }
    defaults.update(kwargs)
    return Asset.objects.create(**defaults)


# ---------------------------------------------------------------------------
# /api/v2/viewer/playlist
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_playlist_requires_internal_auth() -> None:
    """No token → 403. The endpoint must never serve an
    unauthenticated request because it discloses asset URIs (which
    can include credentials inline)."""
    response = Client().get('/api/v2/viewer/playlist')
    assert response.status_code == 403


@pytest.mark.django_db
def test_playlist_rejects_wrong_token() -> None:
    """A malformed / stale token is treated like no token at all —
    same 403, no timing-leak distinction."""
    response = Client().get(
        '/api/v2/viewer/playlist',
        headers={INTERNAL_AUTH_HEADER: 'not-the-real-token'},
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_playlist_empty_when_no_assets() -> None:
    response = Client().get('/api/v2/viewer/playlist', headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body['assets'] == []
    assert body['deadline'] is None
    assert body['now']


@pytest.mark.django_db
def test_playlist_returns_only_active_assets(
    _restore_shuffle_setting: None,
) -> None:
    """Active assets are surfaced, an asset whose start_date is in
    the future is filtered out (Asset.is_active() will return False
    for it)."""
    anthias_settings['shuffle_playlist'] = False
    now = timezone.now()
    _make(asset_id='active', play_order=0)
    _make(
        asset_id='future',
        play_order=1,
        start_date=now + timedelta(days=1),
        end_date=now + timedelta(days=2),
    )
    response = Client().get('/api/v2/viewer/playlist', headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    asset_ids = [a['asset_id'] for a in body['assets']]
    assert asset_ids == ['active']


@pytest.mark.django_db
def test_playlist_orders_by_play_order_when_not_shuffled(
    _restore_shuffle_setting: None,
) -> None:
    anthias_settings['shuffle_playlist'] = False
    _make(asset_id='b', play_order=1)
    _make(asset_id='a', play_order=0)
    response = Client().get('/api/v2/viewer/playlist', headers=_auth_headers())
    body = response.json()
    assert [a['asset_id'] for a in body['assets']] == ['a', 'b']


@pytest.mark.django_db
def test_playlist_deadline_is_soonest_active_end_date(
    _restore_shuffle_setting: None,
) -> None:
    """Two active assets: deadline is the earlier end_date because
    that's when the first one drops out of the active set."""
    anthias_settings['shuffle_playlist'] = False
    now = timezone.now()
    soonest_end = now + timedelta(hours=1)
    _make(asset_id='soonest', play_order=0, end_date=soonest_end)
    _make(
        asset_id='later',
        play_order=1,
        end_date=now + timedelta(days=2),
    )
    response = Client().get('/api/v2/viewer/playlist', headers=_auth_headers())
    body = response.json()
    # ISO8601 round-trip: DRF emits microseconds + tz suffix
    assert body['deadline'].startswith(soonest_end.strftime('%Y-%m-%dT%H:%M'))


@pytest.mark.django_db
def test_playlist_deadline_picks_inactive_start_date_when_scheduled(
    _restore_shuffle_setting: None,
) -> None:
    """If the soonest future boundary is an *inactive* asset's
    start_date (not the active one's end_date), deadline must point
    at it so the viewer re-evaluates when the scheduled asset
    becomes active."""
    anthias_settings['shuffle_playlist'] = False
    now = timezone.now()
    scheduled_start = now + timedelta(hours=1)
    _make(
        asset_id='active',
        play_order=0,
        end_date=now + timedelta(days=2),
    )
    _make(
        asset_id='scheduled',
        play_order=1,
        start_date=scheduled_start,
        end_date=now + timedelta(days=3),
    )
    response = Client().get('/api/v2/viewer/playlist', headers=_auth_headers())
    body = response.json()
    assert body['deadline'].startswith(
        scheduled_start.strftime('%Y-%m-%dT%H:%M')
    )
    # Only the active one is returned in the assets list.
    assert [a['asset_id'] for a in body['assets']] == ['active']


@pytest.mark.django_db
def test_playlist_deadline_caps_when_asset_has_time_window(
    _restore_shuffle_setting: None,
) -> None:
    """An asset with a play_time window can transition active state
    without crossing a start/end boundary — the deadline must
    include a 60s cap so the viewer comes back to re-check."""
    anthias_settings['shuffle_playlist'] = False
    # Travel to a fixed moment so play_time_from/to behaviour is
    # deterministic regardless of when the test happens to run.
    with time_machine.travel('2026-05-18 12:00:00+00:00', tick=False):
        now = timezone.now()
        _make(
            asset_id='windowed',
            play_order=0,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=30),  # well past the 60s cap
            play_time_from=time(0, 0),
            play_time_to=time(23, 59),
        )
        response = Client().get(
            '/api/v2/viewer/playlist',
            headers=_auth_headers(),
        )
    body = response.json()
    assert body['deadline'] is not None
    # The deadline should be at or before now + 61s (60s cap + the
    # split-second between timezone.now() and response build).
    cap_upper = now + timedelta(seconds=61)
    assert (
        body['deadline'] <= cap_upper.isoformat().replace('+00:00', 'Z')
        or body['deadline'] <= cap_upper.isoformat()
    )


@pytest.mark.django_db
def test_playlist_shuffles_when_setting_enabled(
    _restore_shuffle_setting: None,
) -> None:
    """With shuffle on, asset membership stays the same but order
    is shuffled. With many assets, repeated requests should produce
    at least one non-sorted order — but to keep the test
    deterministic we just verify all assets are present.

    Determinism via membership rather than order: shuffle uses
    SystemRandom which we don't seed, so order is genuinely
    unpredictable; the property we care about is that no asset
    is dropped."""
    anthias_settings['shuffle_playlist'] = True
    for i in range(5):
        _make(asset_id=f'a{i}', play_order=i)
    response = Client().get('/api/v2/viewer/playlist', headers=_auth_headers())
    body = response.json()
    ids = sorted(a['asset_id'] for a in body['assets'])
    assert ids == [f'a{i}' for i in range(5)]


# ---------------------------------------------------------------------------
# /api/v2/viewer/settings
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_settings_requires_internal_auth() -> None:
    response = Client().get('/api/v2/viewer/settings')
    assert response.status_code == 403


@pytest.mark.django_db
def test_settings_returns_viewer_subset() -> None:
    response = Client().get('/api/v2/viewer/settings', headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    # Narrow on purpose — adding operator fields here would defeat
    # the point of a viewer-scoped endpoint. Keep this strict.
    assert set(body.keys()) == {
        'shuffle_playlist',
        'show_splash',
        'screen_rotation',
        'audio_output',
        'debug_logging',
    }


@pytest.mark.django_db
def test_settings_clamps_screen_rotation() -> None:
    """A hand-edited anthias.conf could leave an out-of-range
    rotation on disk. The endpoint must clamp on read so clients
    only ever see {0, 90, 180, 270}."""
    original = anthias_settings.get('screen_rotation', 0)
    anthias_settings['screen_rotation'] = 45
    try:
        response = Client().get(
            '/api/v2/viewer/settings',
            headers=_auth_headers(),
        )
    finally:
        anthias_settings['screen_rotation'] = original
    body = response.json()
    assert body['screen_rotation'] in (0, 90, 180, 270)
