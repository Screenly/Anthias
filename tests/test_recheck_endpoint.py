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

from django.test import Client, TestCase

from anthias_app.models import Asset
from celery_tasks import (
    ASSET_RECHECK_QUEUE_DEBOUNCE_S,
    asset_recheck_queue_key,
    r as redis_conn,
)


class AssetRecheckEndpointTest(TestCase):
    def setUp(self) -> None:
        Asset.objects.all().delete()
        # Clear the per-asset queue-debounce key so each test starts
        # fresh — otherwise a prior test's SETNX would suppress the
        # current test's expected enqueue.
        redis_conn.delete(asset_recheck_queue_key('a1'))

    def tearDown(self) -> None:
        Asset.objects.all().delete()
        redis_conn.delete(asset_recheck_queue_key('a1'))

    def _make(self, **kwargs: object) -> Asset:
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

    def test_returns_404_for_unknown_asset(self) -> None:
        with mock.patch('celery_tasks.revalidate_asset_url.delay') as m:
            response = Client().post('/api/v2/assets/nope/recheck')
        self.assertEqual(response.status_code, 404)
        m.assert_not_called()

    def test_enqueues_task_when_no_lock_held(self) -> None:
        """Fresh asset, no recent recheck: SETNX succeeds → enqueue."""
        self._make()
        with mock.patch('celery_tasks.revalidate_asset_url.delay') as m:
            response = Client().post('/api/v2/assets/a1/recheck')
        self.assertEqual(response.status_code, 202)
        m.assert_called_once_with('a1')

    def test_skips_enqueue_when_queue_debounce_held(self) -> None:
        """Pinned: a viewer rotating past an unreachable asset every
        few seconds would otherwise enqueue a Celery task on every
        rotation, each one no-oping at the task's own cooldown gate.
        The endpoint's SETNX queue-debounce prevents this — same 202
        to the caller because the recheck is effectively up-to-date."""
        self._make()
        # Simulate the queue-debounce key being held from a prior
        # endpoint hit within the debounce window.
        redis_conn.set(
            asset_recheck_queue_key('a1'),
            '1',
            ex=ASSET_RECHECK_QUEUE_DEBOUNCE_S,
        )
        with mock.patch('celery_tasks.revalidate_asset_url.delay') as m:
            response = Client().post('/api/v2/assets/a1/recheck')
        self.assertEqual(response.status_code, 202)
        m.assert_not_called()

    def test_back_to_back_calls_only_enqueue_once(self) -> None:
        """End-to-end: two endpoint hits within the debounce window
        result in exactly one enqueue. This is the failure mode that
        a timestamp-based check couldn't catch — the timestamp only
        updates after the task finishes, so two near-simultaneous
        endpoint hits would both read the stale value and each
        enqueue."""
        self._make()
        with mock.patch('celery_tasks.revalidate_asset_url.delay') as m:
            r1 = Client().post('/api/v2/assets/a1/recheck')
            r2 = Client().post('/api/v2/assets/a1/recheck')
        self.assertEqual(r1.status_code, 202)
        self.assertEqual(r2.status_code, 202)
        self.assertEqual(m.call_count, 1)

    def test_endpoint_is_unauthenticated(self) -> None:
        """Pinned design choice: viewer can't BasicAuth, so decorator
        would silently 401 and break the on-demand mitigation. This
        test makes a future flip to @authorized fail fast."""
        self._make()
        with mock.patch('celery_tasks.revalidate_asset_url.delay'):
            # No auth headers, no session — must still work.
            response = Client().post('/api/v2/assets/a1/recheck')
        self.assertEqual(response.status_code, 202)
