import os
import tempfile
import time
from collections.abc import Iterator
from os import path
from unittest import mock

import pytest

import celery_tasks as celery_tasks_module
from anthias_app.models import Asset
from celery_tasks import celery as celeryapp
from celery_tasks import (
    cleanup,
    get_display_power,
    reboot_anthias,
    send_telemetry_task,
    shutdown_anthias,
)
from settings import settings


def _set_mtime(file_path: str, age_seconds: int) -> None:
    target = time.time() - age_seconds
    os.utime(file_path, (target, target))


@pytest.fixture
def asset_dir() -> Iterator[str]:
    """
    Covers the orphan-file sweep added for forum 6636 / GH #2657.

    cleanup() reads settings['assetdir'] directly, so each test points
    that at a fresh tempdir, runs the task, and inspects what survived.
    """
    celeryapp.conf.update(
        CELERY_ALWAYS_EAGER=True,
        CELERY_RESULT_BACKEND='',
        CELERY_BROKER_URL='',
    )
    Asset.objects.all().delete()
    tmpdir = tempfile.TemporaryDirectory()
    original_assetdir = settings['assetdir']
    settings['assetdir'] = tmpdir.name
    try:
        yield tmpdir.name
    finally:
        settings['assetdir'] = original_assetdir
        tmpdir.cleanup()
        Asset.objects.all().delete()


def _touch(asset_dir: str, name: str, age_seconds: int = 0) -> str:
    full = path.join(asset_dir, name)
    with open(full, 'w') as fh:
        fh.write('x')
    if age_seconds:
        _set_mtime(full, age_seconds)
    return full


def _make_asset(asset_id: str, uri: str) -> None:
    Asset.objects.create(
        asset_id=asset_id,
        name=asset_id,
        uri=uri,
        mimetype='image',
        duration=10,
    )


@pytest.mark.django_db
def test_fresh_tmp_is_retained(asset_dir: str) -> None:
    """A .tmp younger than 1h is mid-upload and must survive."""
    fresh = _touch(asset_dir, 'upload.tmp', age_seconds=10 * 60)
    cleanup.apply()
    assert path.exists(fresh)


@pytest.mark.django_db
def test_stale_tmp_is_removed(asset_dir: str) -> None:
    """A .tmp older than 1h is an abandoned upload and gets swept."""
    stale = _touch(asset_dir, 'abandoned.tmp', age_seconds=2 * 60 * 60)
    cleanup.apply()
    assert not path.exists(stale)


@pytest.mark.django_db
def test_orphan_file_is_removed(asset_dir: str) -> None:
    """No Asset row references it and it's older than the 1h guard."""
    orphan = _touch(asset_dir, 'orphan.png', age_seconds=2 * 60 * 60)
    cleanup.apply()
    assert not path.exists(orphan)


@pytest.mark.django_db
def test_referenced_file_is_preserved(asset_dir: str) -> None:
    """Even past the 1h guard, a referenced file must survive."""
    kept = _touch(asset_dir, 'kept.png', age_seconds=2 * 60 * 60)
    _make_asset('kept', kept)
    cleanup.apply()
    assert path.exists(kept)


@pytest.mark.django_db
def test_legacy_symlinked_uri_is_preserved(asset_dir: str) -> None:
    """
    Pre-rebrand DB rows reference paths like
    ~/screenly_assets/foo.png, which after upgrade is a symlink to
    ~/anthias_assets/foo.png. The orphan sweep must recognize the
    underlying file as referenced rather than treating it as junk.
    """
    kept = _touch(asset_dir, 'legacy.png', age_seconds=2 * 60 * 60)
    legacy_dir = asset_dir + '_legacy_link'
    os.symlink(asset_dir, legacy_dir)
    try:
        legacy_uri = path.join(legacy_dir, 'legacy.png')
        _make_asset('legacy', legacy_uri)
        cleanup.apply()
        assert path.exists(kept)
    finally:
        os.unlink(legacy_dir)


@pytest.mark.django_db
def test_fresh_ytdl_sidecar_is_retained(asset_dir: str) -> None:
    """In-flight yt-dlp sidecars (<1h) must survive the sweep."""
    fresh_part = _touch(asset_dir, 'video.mp4.part', age_seconds=10 * 60)
    fresh_info = _touch(asset_dir, 'video.info.json', age_seconds=10 * 60)
    cleanup.apply()
    assert path.exists(fresh_part)
    assert path.exists(fresh_info)


@pytest.mark.django_db
def test_stale_ytdl_sidecar_is_removed(asset_dir: str) -> None:
    """Old sidecars from abandoned downloads should not pile up."""
    stale_part = _touch(asset_dir, 'old.mp4.part', age_seconds=2 * 60 * 60)
    stale_info = _touch(asset_dir, 'old.info.json', age_seconds=2 * 60 * 60)
    cleanup.apply()
    assert not path.exists(stale_part)
    assert not path.exists(stale_info)


def test_cleanup_returns_when_assetdir_missing(tmp_path: str) -> None:
    """cleanup() bails early if settings['assetdir'] doesn't exist."""
    nonexistent = '/tmp/nonexistent-anthias-cleanup-dir-xyz'
    if path.isdir(nonexistent):
        os.rmdir(nonexistent)
    original = settings['assetdir']
    settings['assetdir'] = nonexistent
    try:
        with mock.patch.object(celery_tasks_module, 'sh') as mock_sh:
            cleanup.apply()
        # `sh.find` should never have been invoked on a missing dir.
        mock_sh.find.assert_not_called()
    finally:
        settings['assetdir'] = original


def test_get_display_power_writes_redis() -> None:
    """The Celery task wraps diagnostics.get_display_power and persists."""
    fake_redis = mock.MagicMock()
    with (
        mock.patch.object(celery_tasks_module, 'r', fake_redis),
        mock.patch(
            'celery_tasks.diagnostics.get_display_power',
            return_value=True,
        ),
    ):
        get_display_power.apply()

    fake_redis.set.assert_called_once_with('display_power', True)
    fake_redis.expire.assert_called_once_with('display_power', 3600)


def test_send_telemetry_task_dispatches() -> None:
    """The hourly Celery task is a thin wrapper over lib.telemetry."""
    with mock.patch.object(celery_tasks_module, 'send_telemetry') as mock_send:
        send_telemetry_task.apply()
    mock_send.assert_called_once_with()


def test_reboot_anthias_publishes_hostcmd_off_balena() -> None:
    fake_redis = mock.MagicMock()
    with (
        mock.patch.object(celery_tasks_module, 'r', fake_redis),
        mock.patch.object(
            celery_tasks_module, 'is_balena_app', return_value=False
        ),
    ):
        reboot_anthias.apply()
    fake_redis.publish.assert_called_once_with('hostcmd', 'reboot')


def test_reboot_anthias_uses_balena_supervisor_on_balena() -> None:
    with (
        mock.patch.object(
            celery_tasks_module, 'is_balena_app', return_value=True
        ),
        mock.patch.object(
            celery_tasks_module, 'reboot_via_balena_supervisor'
        ) as mock_reboot,
    ):
        reboot_anthias.apply()
    mock_reboot.assert_called_once()


def test_shutdown_anthias_publishes_hostcmd_off_balena() -> None:
    fake_redis = mock.MagicMock()
    with (
        mock.patch.object(celery_tasks_module, 'r', fake_redis),
        mock.patch.object(
            celery_tasks_module, 'is_balena_app', return_value=False
        ),
    ):
        shutdown_anthias.apply()
    fake_redis.publish.assert_called_once_with('hostcmd', 'shutdown')


def test_shutdown_anthias_uses_balena_supervisor_on_balena() -> None:
    with (
        mock.patch.object(
            celery_tasks_module, 'is_balena_app', return_value=True
        ),
        mock.patch.object(
            celery_tasks_module, 'shutdown_via_balena_supervisor'
        ) as mock_shutdown,
    ):
        shutdown_anthias.apply()
    mock_shutdown.assert_called_once()
