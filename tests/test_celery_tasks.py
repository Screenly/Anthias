import os
import tempfile
import time
from os import path

from django.test import TestCase

from anthias_app.models import Asset
from celery_tasks import celery as celeryapp
from celery_tasks import cleanup
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
