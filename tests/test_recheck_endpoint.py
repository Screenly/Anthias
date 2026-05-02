"""
Tests for POST /api/v2/assets/<asset_id>/recheck.

The endpoint is a thin wrapper around the on-demand
``revalidate_asset_url`` Celery task. It exists so the viewer can
nudge the server when it skips an asset marked unreachable, without
the viewer holding any auth credentials. Cooldown / queue-churn
protection is implemented as a per-asset SETNX queue-debounce key in
Redis (separate from the task's own per-asset cooldown lock).
"""

from unittest import mock

import pytest
from django.test import Client

import api.views.v2 as v2_module
from anthias_app.models import Asset
from celery_tasks import asset_recheck_queue_key


def _make(**kwargs: object) -> Asset:
    defaults: dict[str, object] = {
        'asset_id': 'a1',
        'name': 'a1',
        'uri': 'https://example.com/x.png',
        'mimetype': 'image',
        'duration': 10,
        'is_enabled': True,
    }
    defaults.update(kwargs)
    return Asset.objects.create(**defaults)


@pytest.mark.django_db
def test_returns_404_for_unknown_asset() -> None:
    with mock.patch('celery_tasks.revalidate_asset_url.delay') as m:
        response = Client().post('/api/v2/assets/nope/recheck')
    assert response.status_code == 404
    m.assert_not_called()


@pytest.mark.django_db
def test_enqueues_task_when_no_lock_held() -> None:
    """Fresh asset, no recent recheck: SETNX succeeds → enqueue."""
    _make()
    with mock.patch('celery_tasks.revalidate_asset_url.delay') as m:
        response = Client().post('/api/v2/assets/a1/recheck')
    assert response.status_code == 202
    m.assert_called_once_with('a1')


@pytest.mark.django_db
def test_skips_enqueue_when_queue_debounce_held() -> None:
    """Pinned: a viewer rotating past an unreachable asset every few
    seconds would otherwise enqueue a Celery task on every rotation,
    each one no-oping at the task's own cooldown gate. The endpoint's
    SETNX queue-debounce prevents this — same 202 to the caller
    because the recheck is effectively up-to-date."""
    _make()
    # Pre-acquire the queue-debounce key on the same fake the
    # endpoint reads from.
    v2_module.r.set(asset_recheck_queue_key('a1'), '1')
    with mock.patch('celery_tasks.revalidate_asset_url.delay') as m:
        response = Client().post('/api/v2/assets/a1/recheck')
    assert response.status_code == 202
    m.assert_not_called()


@pytest.mark.django_db
def test_back_to_back_calls_only_enqueue_once() -> None:
    """End-to-end: two endpoint hits within the debounce window result
    in exactly one enqueue. This is the failure mode that a
    timestamp-based check couldn't catch — the timestamp only updates
    after the task finishes, so two near-simultaneous endpoint hits
    would both read the stale value and each enqueue."""
    _make()
    with mock.patch('celery_tasks.revalidate_asset_url.delay') as m:
        r1 = Client().post('/api/v2/assets/a1/recheck')
        r2 = Client().post('/api/v2/assets/a1/recheck')
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert m.call_count == 1


@pytest.mark.django_db
def test_endpoint_is_unauthenticated() -> None:
    """Pinned design choice: viewer can't BasicAuth, so decorator would
    silently 401 and break the on-demand mitigation. This test makes a
    future flip to @authorized fail fast."""
    _make()
    with mock.patch('celery_tasks.revalidate_asset_url.delay'):
        # No auth headers, no session — must still work.
        response = Client().post('/api/v2/assets/a1/recheck')
    assert response.status_code == 202
