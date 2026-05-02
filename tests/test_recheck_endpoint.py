"""
Tests for POST /api/v2/assets/<asset_id>/recheck.

The endpoint is a thin wrapper around the on-demand
``revalidate_asset_url`` Celery task. It exists so the viewer can
nudge the server when it skips an asset marked unreachable, without
the viewer holding any auth credentials. Because it's unauth'd, the
endpoint pre-filters with the same per-asset cooldown the task
itself enforces — this avoids queue churn from a viewer that rotates
quickly past the same unreachable asset.
"""

from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone

from anthias_app.models import Asset
from celery_tasks import RECHECK_COOLDOWN_S


class AssetRecheckEndpointTest(TestCase):
    def setUp(self) -> None:
        Asset.objects.all().delete()

    def tearDown(self) -> None:
        Asset.objects.all().delete()

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

    def test_enqueues_task_when_no_prior_check(self) -> None:
        """Fresh asset with no last_reachability_check: cooldown can't
        apply, so the request enqueues unconditionally."""
        self._make(last_reachability_check=None)
        with mock.patch('celery_tasks.revalidate_asset_url.delay') as m:
            response = Client().post('/api/v2/assets/a1/recheck')
        self.assertEqual(response.status_code, 202)
        m.assert_called_once_with('a1')

    def test_enqueues_task_when_cooldown_elapsed(self) -> None:
        old = timezone.now() - timedelta(seconds=RECHECK_COOLDOWN_S + 5)
        self._make(last_reachability_check=old)
        with mock.patch('celery_tasks.revalidate_asset_url.delay') as m:
            response = Client().post('/api/v2/assets/a1/recheck')
        self.assertEqual(response.status_code, 202)
        m.assert_called_once_with('a1')

    def test_skips_enqueue_when_cooldown_active(self) -> None:
        """Pinned: a viewer rotating past an unreachable asset every few
        seconds would otherwise enqueue a Celery task on every rotation,
        each one no-oping immediately under the task's own cooldown.
        Pre-filter at the endpoint to avoid queue churn — same 202 to
        the caller because the recheck is effectively up-to-date."""
        recent = timezone.now() - timedelta(seconds=RECHECK_COOLDOWN_S - 5)
        self._make(last_reachability_check=recent)
        with mock.patch('celery_tasks.revalidate_asset_url.delay') as m:
            response = Client().post('/api/v2/assets/a1/recheck')
        self.assertEqual(response.status_code, 202)
        m.assert_not_called()

    def test_endpoint_is_unauthenticated(self) -> None:
        """Pinned design choice: viewer can't BasicAuth, so decorator
        would silently 401 and break the on-demand mitigation. This
        test makes a future flip to @authorized fail fast."""
        self._make(last_reachability_check=None)
        with mock.patch('celery_tasks.revalidate_asset_url.delay'):
            # No auth headers, no session — must still work.
            response = Client().post('/api/v2/assets/a1/recheck')
        self.assertEqual(response.status_code, 202)
