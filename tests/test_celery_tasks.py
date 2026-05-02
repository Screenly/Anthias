import os
import tempfile
import time
from os import path
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from anthias_app.models import Asset
from celery_tasks import (
    ASSET_REVALIDATION_LOCK_KEY,
    RECHECK_COOLDOWN_S,
    asset_recheck_lock_key,
    celery as celeryapp,
    cleanup,
    r as redis_conn,
    revalidate_asset_url,
    revalidate_asset_urls,
)
from settings import settings


def _set_mtime(file_path: str, age_seconds: int) -> None:
    target = time.time() - age_seconds
    os.utime(file_path, (target, target))


class TestCleanupOrphanSweep(TestCase):
    """
    Covers the orphan-file sweep added for forum 6636 / GH #2657.

    cleanup() reads settings['assetdir'] directly, so each test points
    that at a fresh tempdir, runs the task, and inspects what survived.
    """

    def setUp(self) -> None:
        celeryapp.conf.update(
            CELERY_ALWAYS_EAGER=True,
            CELERY_RESULT_BACKEND='',
            CELERY_BROKER_URL='',
        )
        Asset.objects.all().delete()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.asset_dir = self._tmpdir.name
        self._original_assetdir = settings['assetdir']
        settings['assetdir'] = self.asset_dir

    def tearDown(self) -> None:
        settings['assetdir'] = self._original_assetdir
        self._tmpdir.cleanup()
        Asset.objects.all().delete()

    def _touch(self, name: str, age_seconds: int = 0) -> str:
        full = path.join(self.asset_dir, name)
        with open(full, 'w') as fh:
            fh.write('x')
        if age_seconds:
            _set_mtime(full, age_seconds)
        return full

    def _make_asset(self, asset_id: str, uri: str) -> None:
        Asset.objects.create(
            asset_id=asset_id,
            name=asset_id,
            uri=uri,
            mimetype='image',
            duration=10,
        )

    def test_fresh_tmp_is_retained(self) -> None:
        """A .tmp younger than 1h is mid-upload and must survive."""
        fresh = self._touch('upload.tmp', age_seconds=10 * 60)
        cleanup.apply()
        self.assertTrue(path.exists(fresh))

    def test_stale_tmp_is_removed(self) -> None:
        """A .tmp older than 1h is an abandoned upload and gets swept."""
        stale = self._touch('abandoned.tmp', age_seconds=2 * 60 * 60)
        cleanup.apply()
        self.assertFalse(path.exists(stale))

    def test_orphan_file_is_removed(self) -> None:
        """No Asset row references it and it's older than the 1h guard."""
        orphan = self._touch('orphan.png', age_seconds=2 * 60 * 60)
        cleanup.apply()
        self.assertFalse(path.exists(orphan))

    def test_referenced_file_is_preserved(self) -> None:
        """Even past the 1h guard, a referenced file must survive."""
        kept = self._touch('kept.png', age_seconds=2 * 60 * 60)
        self._make_asset('kept', kept)
        cleanup.apply()
        self.assertTrue(path.exists(kept))

    def test_legacy_symlinked_uri_is_preserved(self) -> None:
        """
        Pre-rebrand DB rows reference paths like
        ~/screenly_assets/foo.png, which after upgrade is a symlink to
        ~/anthias_assets/foo.png. The orphan sweep must recognize the
        underlying file as referenced rather than treating it as junk.
        """
        kept = self._touch('legacy.png', age_seconds=2 * 60 * 60)
        legacy_dir = self._tmpdir.name + '_legacy_link'
        os.symlink(self.asset_dir, legacy_dir)
        try:
            legacy_uri = path.join(legacy_dir, 'legacy.png')
            self._make_asset('legacy', legacy_uri)
            cleanup.apply()
            self.assertTrue(path.exists(kept))
        finally:
            os.unlink(legacy_dir)

    def test_fresh_ytdl_sidecar_is_retained(self) -> None:
        """In-flight yt-dlp sidecars (<1h) must survive the sweep."""
        fresh_part = self._touch('video.mp4.part', age_seconds=10 * 60)
        fresh_info = self._touch('video.info.json', age_seconds=10 * 60)
        cleanup.apply()
        self.assertTrue(path.exists(fresh_part))
        self.assertTrue(path.exists(fresh_info))

    def test_stale_ytdl_sidecar_is_removed(self) -> None:
        """Old sidecars from abandoned downloads should not pile up."""
        stale_part = self._touch('old.mp4.part', age_seconds=2 * 60 * 60)
        stale_info = self._touch('old.info.json', age_seconds=2 * 60 * 60)
        cleanup.apply()
        self.assertFalse(path.exists(stale_part))
        self.assertFalse(path.exists(stale_info))


class TestRevalidateAssetUrls(TestCase):
    """
    Periodic sweep flips Asset.is_reachable based on url_fails. The probe
    itself is exercised by tests/test_utils.py — here we cover the
    dispatch shape: which assets get probed, what gets written back, and
    how exceptions are contained so a single bad asset can't kill the
    sweep.
    """

    def setUp(self) -> None:
        celeryapp.conf.update(
            CELERY_ALWAYS_EAGER=True,
            CELERY_RESULT_BACKEND='',
            CELERY_BROKER_URL='',
        )
        Asset.objects.all().delete()
        # Drop any stale singleton lock from a prior test run that
        # crashed before its finally clause could clean up.
        redis_conn.delete(ASSET_REVALIDATION_LOCK_KEY)

    def tearDown(self) -> None:
        Asset.objects.all().delete()
        redis_conn.delete(ASSET_REVALIDATION_LOCK_KEY)

    def _make_asset(
        self,
        asset_id: str,
        uri: str = 'https://example.com/x.png',
        is_enabled: bool = True,
        is_processing: bool = False,
        skip_asset_check: bool = False,
        is_reachable: bool = True,
    ) -> Asset:
        return Asset.objects.create(
            asset_id=asset_id,
            name=asset_id,
            uri=uri,
            mimetype='image',
            duration=10,
            is_enabled=is_enabled,
            is_processing=is_processing,
            skip_asset_check=skip_asset_check,
            is_reachable=is_reachable,
        )

    def test_marks_unreachable_when_url_fails(self) -> None:
        self._make_asset('a1')
        with mock.patch('celery_tasks.url_fails', return_value=True):
            revalidate_asset_urls.apply()
        self.assertFalse(Asset.objects.get(asset_id='a1').is_reachable)

    def test_marks_reachable_when_url_succeeds(self) -> None:
        self._make_asset('a1', is_reachable=False)
        with mock.patch('celery_tasks.url_fails', return_value=False):
            revalidate_asset_urls.apply()
        self.assertTrue(Asset.objects.get(asset_id='a1').is_reachable)

    def test_updates_last_reachability_check(self) -> None:
        self._make_asset('a1')
        before = timezone.now()
        with mock.patch('celery_tasks.url_fails', return_value=False):
            revalidate_asset_urls.apply()
        last = Asset.objects.get(asset_id='a1').last_reachability_check
        self.assertIsNotNone(last)
        assert last is not None
        self.assertGreaterEqual(last, before)

    def test_skips_disabled_assets(self) -> None:
        self._make_asset('a1', is_enabled=False, is_reachable=True)
        with mock.patch('celery_tasks.url_fails', return_value=True) as m:
            revalidate_asset_urls.apply()
        m.assert_not_called()
        self.assertTrue(Asset.objects.get(asset_id='a1').is_reachable)

    def test_skips_processing_assets(self) -> None:
        self._make_asset('a1', is_processing=True)
        with mock.patch('celery_tasks.url_fails', return_value=True) as m:
            revalidate_asset_urls.apply()
        m.assert_not_called()
        self.assertTrue(Asset.objects.get(asset_id='a1').is_reachable)

    def test_skip_asset_check_short_circuits_probe(self) -> None:
        """Operator opted out of validation; trust them and don't probe."""
        self._make_asset('a1', skip_asset_check=True)
        with mock.patch('celery_tasks.url_fails') as m:
            revalidate_asset_urls.apply()
        m.assert_not_called()
        self.assertTrue(Asset.objects.get(asset_id='a1').is_reachable)
        # last_reachability_check must NOT be set — the API exposes
        # that field as "last check", and writing it without an
        # actual probe would advertise a check that never happened.
        self.assertIsNone(
            Asset.objects.get(asset_id='a1').last_reachability_check
        )

    def test_local_file_existence_check(self) -> None:
        """Local URIs short-circuit url_fails and check the filesystem."""
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            local = fh.name
        try:
            self._make_asset('a1', uri=local)
            with mock.patch('celery_tasks.url_fails') as m:
                revalidate_asset_urls.apply()
            m.assert_not_called()
            self.assertTrue(Asset.objects.get(asset_id='a1').is_reachable)
        finally:
            os.unlink(local)

        # Same row, file now gone — sweep should mark it unreachable.
        revalidate_asset_urls.apply()
        self.assertFalse(Asset.objects.get(asset_id='a1').is_reachable)

    def test_probe_exception_does_not_kill_sweep(self) -> None:
        """One asset's probe blowing up must not break the others."""
        self._make_asset('boom', uri='https://example.com/boom')
        self._make_asset('ok', uri='https://example.com/ok')

        def fake_url_fails(url: str) -> bool:
            if 'boom' in url:
                raise RuntimeError('synthetic')
            return False

        with mock.patch('celery_tasks.url_fails', side_effect=fake_url_fails):
            revalidate_asset_urls.apply()

        # 'boom' is left as-is (we don't have a probe result to write),
        # but 'ok' must still have been processed.
        self.assertTrue(Asset.objects.get(asset_id='ok').is_reachable)
        self.assertIsNotNone(
            Asset.objects.get(asset_id='ok').last_reachability_check
        )

    def test_lock_prevents_overlap(self) -> None:
        """A second beat tick that fires while a sweep is running must
        be a no-op. Without the lock, two workers would race on the
        same asset rows; in practice on a streaming-heavy playlist a
        sweep can approach the periodic interval and overlap is real.
        """
        self._make_asset('a1', is_reachable=True)
        # Pre-acquire the lock to simulate a sweep already in flight.
        redis_conn.set(ASSET_REVALIDATION_LOCK_KEY, '1', ex=60)
        try:
            with mock.patch('celery_tasks.url_fails', return_value=True) as m:
                revalidate_asset_urls.apply()
            # The sweep saw the lock and exited without probing.
            m.assert_not_called()
            self.assertTrue(Asset.objects.get(asset_id='a1').is_reachable)
        finally:
            redis_conn.delete(ASSET_REVALIDATION_LOCK_KEY)

    def test_lock_release_does_not_clobber_a_different_holder(self) -> None:
        """Pathological case: TTL expires while sweep A is still running,
        sweep B acquires the (now-free) lock with a fresh token, then
        sweep A finishes and hits its finally clause. A's release must
        only delete the lock if its token still matches — else it would
        clobber B's lock and let yet another sweep slip in.
        """
        self._make_asset('a1')

        # Run the sweep, but in the middle of it, simulate the lock
        # being stolen by a different "sweep" (different token).
        def steal_during_sweep(*args: object, **kwargs: object) -> bool:
            # Overwrite the lock value with someone else's token.
            redis_conn.set(ASSET_REVALIDATION_LOCK_KEY, 'someone-else', ex=60)
            return False  # url_fails return — asset is reachable

        with mock.patch(
            'celery_tasks.url_fails', side_effect=steal_during_sweep
        ):
            revalidate_asset_urls.apply()

        # Our sweep's finally clause ran, but the lock value should
        # still be 'someone-else' — the compare-and-delete saw a
        # token mismatch and left the lock in place.
        self.assertEqual(
            redis_conn.get(ASSET_REVALIDATION_LOCK_KEY), 'someone-else'
        )

    def test_lock_released_after_clean_run(self) -> None:
        """The finally clause must release the lock so the next beat
        tick can run. Without it, a successful sweep would block the
        next sweep until the TTL expired."""
        self._make_asset('a1')
        with mock.patch('celery_tasks.url_fails', return_value=False):
            revalidate_asset_urls.apply()
        self.assertIsNone(redis_conn.get(ASSET_REVALIDATION_LOCK_KEY))


class TestRevalidateAssetUrl(TestCase):
    """
    On-demand single-asset probe, called from the
    /api/v2/assets/<id>/recheck endpoint. Cooldown- and concurrency-
    safe via an atomic Redis SETNX lock per asset (TTL =
    RECHECK_COOLDOWN_S). Replaces an earlier timestamp-based check
    that was racy: multiple workers could read the same stale
    last_reachability_check and each decide they should run a probe.
    """

    def setUp(self) -> None:
        celeryapp.conf.update(
            CELERY_ALWAYS_EAGER=True,
            CELERY_RESULT_BACKEND='',
            CELERY_BROKER_URL='',
        )
        Asset.objects.all().delete()
        # Drop any per-asset cooldown lock from a prior test so this
        # test starts with a clean slate.
        redis_conn.delete(asset_recheck_lock_key('a1'))

    def tearDown(self) -> None:
        Asset.objects.all().delete()
        redis_conn.delete(asset_recheck_lock_key('a1'))

    def _make(self, **kwargs: object) -> Asset:
        defaults = {
            'asset_id': 'a1',
            'name': 'a1',
            'uri': 'https://example.com/x.png',
            'mimetype': 'image',
            'duration': 10,
            'is_enabled': True,
        }
        defaults.update(kwargs)
        return Asset.objects.create(**defaults)

    def test_no_op_when_asset_does_not_exist(self) -> None:
        with mock.patch('celery_tasks.url_fails') as m:
            revalidate_asset_url.apply(args=('nope',))
        m.assert_not_called()

    def test_flips_is_reachable(self) -> None:
        self._make(is_reachable=True)
        with mock.patch('celery_tasks.url_fails', return_value=True):
            revalidate_asset_url.apply(args=('a1',))
        self.assertFalse(Asset.objects.get(asset_id='a1').is_reachable)

    def test_cooldown_lock_prevents_back_to_back_probes(self) -> None:
        """SETNX cooldown gate: if the per-asset lock is already held
        (someone else just probed within RECHECK_COOLDOWN_S), this
        task must no-op without calling url_fails."""
        self._make(is_reachable=False)
        # Pre-acquire the cooldown lock to simulate a recent probe.
        redis_conn.set(
            asset_recheck_lock_key('a1'),
            '1',
            ex=RECHECK_COOLDOWN_S,
        )
        with mock.patch('celery_tasks.url_fails', return_value=False) as m:
            revalidate_asset_url.apply(args=('a1',))
        m.assert_not_called()
        # Field is unchanged.
        self.assertFalse(Asset.objects.get(asset_id='a1').is_reachable)

    def test_acquires_lock_when_running(self) -> None:
        """The task must SETNX the cooldown lock before probing — that
        gate is what prevents concurrent ffprobe calls for the same
        asset across workers."""
        self._make()
        with mock.patch('celery_tasks.url_fails', return_value=False):
            revalidate_asset_url.apply(args=('a1',))
        self.assertEqual(redis_conn.get(asset_recheck_lock_key('a1')), '1')

    def test_skips_disabled_asset(self) -> None:
        """Mirror sweep filter: probing a disabled asset would write
        state that's immediately moot."""
        self._make(is_enabled=False, is_reachable=True)
        with mock.patch('celery_tasks.url_fails', return_value=True) as m:
            revalidate_asset_url.apply(args=('a1',))
        m.assert_not_called()
        self.assertTrue(Asset.objects.get(asset_id='a1').is_reachable)

    def test_skips_processing_asset(self) -> None:
        """Mirror sweep filter: an in-flight youtube_asset download
        can still be writing the file out, so a probe is meaningless."""
        self._make(is_processing=True)
        with mock.patch('celery_tasks.url_fails', return_value=True) as m:
            revalidate_asset_url.apply(args=('a1',))
        m.assert_not_called()
        self.assertTrue(Asset.objects.get(asset_id='a1').is_reachable)

    def test_skips_skip_asset_check_asset(self) -> None:
        """Operator opted out of validation; matches sweep behavior of
        not touching is_reachable / last_reachability_check."""
        self._make(skip_asset_check=True, is_reachable=True)
        with mock.patch('celery_tasks.url_fails') as m:
            revalidate_asset_url.apply(args=('a1',))
        m.assert_not_called()
        # Timestamp must not be advertised as a "successful check"
        # since no probe ran.
        self.assertIsNone(
            Asset.objects.get(asset_id='a1').last_reachability_check
        )

    def test_cooldown_elapsed_allows_recheck(self) -> None:
        """No lock held → SETNX succeeds → probe runs."""
        self._make(is_reachable=False)
        with mock.patch('celery_tasks.url_fails', return_value=False):
            revalidate_asset_url.apply(args=('a1',))
        self.assertTrue(Asset.objects.get(asset_id='a1').is_reachable)
