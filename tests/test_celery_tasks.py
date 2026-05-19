import os
import tempfile
import time
from collections.abc import Iterator
from os import path
from unittest import mock

import pytest

import anthias_server.celery_tasks as celery_tasks_module
from anthias_server.app.models import Asset
from anthias_server.celery_tasks import celery as celeryapp
from anthias_server.celery_tasks import (
    ASSET_REVALIDATION_LOCK_KEY,
    asset_recheck_lock_key,
    cleanup,
    download_youtube_asset,
    get_display_power,
    probe_video_duration,
    reboot_anthias,
    revalidate_asset_url,
    revalidate_asset_urls,
    send_telemetry_task,
    shutdown_anthias,
)
from anthias_server.settings import settings


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


def test_cleanup_returns_when_assetdir_missing() -> None:
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
            'anthias_server.celery_tasks.diagnostics.get_display_power',
            return_value=True,
        ),
    ):
        get_display_power.apply()

    fake_redis.set.assert_called_once_with('display_power', True)
    fake_redis.expire.assert_called_once_with('display_power', 3600)


def test_send_telemetry_task_dispatches() -> None:
    """The hourly Celery task is a thin wrapper over anthias_server.lib.telemetry."""
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


# ---------------------------------------------------------------------------
# revalidate_asset_urls (periodic sweep)
# ---------------------------------------------------------------------------


def _make_revalidation_asset(
    asset_id: str = 'a1',
    *,
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


@pytest.fixture
def eager_celery() -> None:
    """
    Periodic sweep flips Asset.is_reachable based on url_fails. The probe
    itself is exercised by tests/test_utils.py — here we cover the
    dispatch shape: which assets get probed, what gets written back, and
    how exceptions are contained so a single bad asset can't kill the
    sweep.
    """
    celeryapp.conf.update(
        CELERY_ALWAYS_EAGER=True,
        CELERY_RESULT_BACKEND='',
        CELERY_BROKER_URL='',
    )
    Asset.objects.all().delete()


@pytest.mark.django_db
def test_sweep_marks_unreachable_when_url_fails(eager_celery: None) -> None:
    _make_revalidation_asset()
    with mock.patch(
        'anthias_server.celery_tasks.url_fails', return_value=True
    ):
        revalidate_asset_urls.apply()
    assert not Asset.objects.get(asset_id='a1').is_reachable


@pytest.mark.django_db
def test_sweep_marks_reachable_when_url_succeeds(eager_celery: None) -> None:
    _make_revalidation_asset(is_reachable=False)
    with mock.patch(
        'anthias_server.celery_tasks.url_fails', return_value=False
    ):
        revalidate_asset_urls.apply()
    assert Asset.objects.get(asset_id='a1').is_reachable


@pytest.mark.django_db
def test_sweep_updates_last_reachability_check(eager_celery: None) -> None:
    from django.utils import timezone

    _make_revalidation_asset()
    before = timezone.now()
    with mock.patch(
        'anthias_server.celery_tasks.url_fails', return_value=False
    ):
        revalidate_asset_urls.apply()
    last = Asset.objects.get(asset_id='a1').last_reachability_check
    assert last is not None
    assert last >= before


@pytest.mark.django_db
def test_sweep_skips_disabled_assets(eager_celery: None) -> None:
    _make_revalidation_asset(is_enabled=False, is_reachable=True)
    with mock.patch(
        'anthias_server.celery_tasks.url_fails', return_value=True
    ) as m:
        revalidate_asset_urls.apply()
    m.assert_not_called()
    assert Asset.objects.get(asset_id='a1').is_reachable


@pytest.mark.django_db
def test_sweep_skips_processing_assets(eager_celery: None) -> None:
    _make_revalidation_asset(is_processing=True)
    with mock.patch(
        'anthias_server.celery_tasks.url_fails', return_value=True
    ) as m:
        revalidate_asset_urls.apply()
    m.assert_not_called()
    assert Asset.objects.get(asset_id='a1').is_reachable


@pytest.mark.django_db
def test_sweep_skips_skip_asset_check_assets_entirely(
    eager_celery: None,
) -> None:
    """Operator opted out of validation; trust them and don't probe.
    Critically, last_reachability_check must NOT be set — the API
    exposes that field as 'last check' and writing it without an
    actual probe would advertise a check that never happened."""
    _make_revalidation_asset(skip_asset_check=True)
    with mock.patch('anthias_server.celery_tasks.url_fails') as m:
        revalidate_asset_urls.apply()
    m.assert_not_called()
    assert Asset.objects.get(asset_id='a1').is_reachable
    assert Asset.objects.get(asset_id='a1').last_reachability_check is None


@pytest.mark.django_db
def test_sweep_local_file_existence_check(eager_celery: None) -> None:
    """Local URIs short-circuit url_fails and check the filesystem."""
    with tempfile.NamedTemporaryFile(delete=False) as fh:
        local = fh.name
    try:
        _make_revalidation_asset(uri=local)
        with mock.patch('anthias_server.celery_tasks.url_fails') as m:
            revalidate_asset_urls.apply()
        m.assert_not_called()
        assert Asset.objects.get(asset_id='a1').is_reachable
    finally:
        os.unlink(local)

    # Same row, file now gone — sweep should mark it unreachable.
    revalidate_asset_urls.apply()
    assert not Asset.objects.get(asset_id='a1').is_reachable


@pytest.mark.django_db
def test_sweep_probe_exception_does_not_kill_sweep(
    eager_celery: None,
) -> None:
    """One asset's probe blowing up must not break the others."""
    _make_revalidation_asset('boom', uri='https://example.com/boom')
    _make_revalidation_asset('ok', uri='https://example.com/ok')

    def fake_url_fails(url: str) -> bool:
        if 'boom' in url:
            raise RuntimeError('synthetic')
        return False

    with mock.patch(
        'anthias_server.celery_tasks.url_fails', side_effect=fake_url_fails
    ):
        revalidate_asset_urls.apply()

    # 'boom' is left as-is (we don't have a probe result to write),
    # but 'ok' must still have been processed.
    assert Asset.objects.get(asset_id='ok').is_reachable
    assert Asset.objects.get(asset_id='ok').last_reachability_check is not None


@pytest.mark.django_db
def test_sweep_lock_prevents_overlap(eager_celery: None) -> None:
    """A second beat tick that fires while a sweep is running must
    be a no-op. Without the lock, two workers would race on the same
    asset rows; in practice on a streaming-heavy playlist a sweep can
    approach the periodic interval and overlap is real."""
    _make_revalidation_asset()
    # Pre-acquire the lock to simulate a sweep already in flight.
    celery_tasks_module.r.set(ASSET_REVALIDATION_LOCK_KEY, 'someone-else')
    with mock.patch(
        'anthias_server.celery_tasks.url_fails', return_value=True
    ) as m:
        revalidate_asset_urls.apply()
    # The sweep saw the lock and exited without probing.
    m.assert_not_called()
    assert Asset.objects.get(asset_id='a1').is_reachable


@pytest.mark.django_db
def test_sweep_lock_release_does_not_clobber_different_holder(
    eager_celery: None,
) -> None:
    """Pathological: TTL expires while sweep A is still running, sweep
    B acquires the (now-free) lock with a fresh token, then sweep A
    finishes and hits its finally clause. A's release must only delete
    the lock if its token still matches — else it would clobber B's
    lock and let yet another sweep slip in."""
    _make_revalidation_asset()

    def steal_during_sweep(*args: object, **kwargs: object) -> bool:
        # Overwrite the lock value mid-sweep to simulate B taking over.
        celery_tasks_module.r.set(ASSET_REVALIDATION_LOCK_KEY, 'someone-else')
        return False  # url_fails return — asset is reachable

    with mock.patch(
        'anthias_server.celery_tasks.url_fails', side_effect=steal_during_sweep
    ):
        revalidate_asset_urls.apply()

    # Compare-and-delete saw a token mismatch and left the lock alone.
    assert (
        celery_tasks_module.r.get(ASSET_REVALIDATION_LOCK_KEY)
        == 'someone-else'
    )


@pytest.mark.django_db
def test_sweep_lock_released_after_clean_run(eager_celery: None) -> None:
    """The finally clause must release the lock so the next beat tick
    can run."""
    _make_revalidation_asset()
    with mock.patch(
        'anthias_server.celery_tasks.url_fails', return_value=False
    ):
        revalidate_asset_urls.apply()
    assert celery_tasks_module.r.get(ASSET_REVALIDATION_LOCK_KEY) is None


# ---------------------------------------------------------------------------
# revalidate_asset_url (on-demand single-asset task)
# ---------------------------------------------------------------------------


@pytest.fixture
def eager_celery_recheck() -> None:
    """
    On-demand single-asset probe. Cooldown- and concurrency-safe via
    an atomic Redis SETNX lock per asset (TTL = RECHECK_COOLDOWN_S).
    """
    celeryapp.conf.update(
        CELERY_ALWAYS_EAGER=True,
        CELERY_RESULT_BACKEND='',
        CELERY_BROKER_URL='',
    )
    Asset.objects.all().delete()


def _make_recheck_asset(**kwargs: object) -> Asset:
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
def test_recheck_no_op_when_asset_does_not_exist(
    eager_celery_recheck: None,
) -> None:
    with mock.patch('anthias_server.celery_tasks.url_fails') as m:
        revalidate_asset_url.apply(args=('nope',))
    m.assert_not_called()
    assert celery_tasks_module.r.get(asset_recheck_lock_key('nope')) is None


@pytest.mark.django_db
def test_recheck_flips_is_reachable(eager_celery_recheck: None) -> None:
    _make_recheck_asset(is_reachable=True)
    with mock.patch(
        'anthias_server.celery_tasks.url_fails', return_value=True
    ):
        revalidate_asset_url.apply(args=('a1',))
    assert not Asset.objects.get(asset_id='a1').is_reachable


@pytest.mark.django_db
def test_recheck_lock_prevents_back_to_back_probes(
    eager_celery_recheck: None,
) -> None:
    """SETNX cooldown gate: if the per-asset lock is already held
    (someone else just probed within RECHECK_COOLDOWN_S), this task
    must no-op without calling url_fails."""
    _make_recheck_asset(is_reachable=False)
    # Pre-acquire the cooldown lock to simulate a recent probe.
    celery_tasks_module.r.set(asset_recheck_lock_key('a1'), '1')
    with mock.patch(
        'anthias_server.celery_tasks.url_fails', return_value=False
    ) as m:
        revalidate_asset_url.apply(args=('a1',))
    m.assert_not_called()
    assert not Asset.objects.get(asset_id='a1').is_reachable


@pytest.mark.django_db
def test_recheck_acquires_lock_when_running(
    eager_celery_recheck: None,
) -> None:
    """The task must SETNX the cooldown lock before probing — that
    gate is what prevents concurrent ffprobe calls for the same asset
    across workers."""
    _make_recheck_asset()
    with mock.patch(
        'anthias_server.celery_tasks.url_fails', return_value=False
    ):
        revalidate_asset_url.apply(args=('a1',))
    assert celery_tasks_module.r.get(asset_recheck_lock_key('a1')) == '1'


@pytest.mark.django_db
def test_recheck_skips_disabled_asset(
    eager_celery_recheck: None,
) -> None:
    _make_recheck_asset(is_enabled=False, is_reachable=True)
    with mock.patch(
        'anthias_server.celery_tasks.url_fails', return_value=True
    ) as m:
        revalidate_asset_url.apply(args=('a1',))
    m.assert_not_called()
    assert Asset.objects.get(asset_id='a1').is_reachable
    assert celery_tasks_module.r.get(asset_recheck_lock_key('a1')) is None


@pytest.mark.django_db
def test_recheck_skips_processing_asset(
    eager_celery_recheck: None,
) -> None:
    _make_recheck_asset(is_processing=True)
    with mock.patch(
        'anthias_server.celery_tasks.url_fails', return_value=True
    ) as m:
        revalidate_asset_url.apply(args=('a1',))
    m.assert_not_called()
    assert Asset.objects.get(asset_id='a1').is_reachable
    assert celery_tasks_module.r.get(asset_recheck_lock_key('a1')) is None


@pytest.mark.django_db
def test_recheck_skips_skip_asset_check_asset(
    eager_celery_recheck: None,
) -> None:
    """Operator opted out of validation; matches sweep behavior of not
    touching is_reachable / last_reachability_check."""
    _make_recheck_asset(skip_asset_check=True, is_reachable=True)
    with mock.patch('anthias_server.celery_tasks.url_fails') as m:
        revalidate_asset_url.apply(args=('a1',))
    m.assert_not_called()
    assert Asset.objects.get(asset_id='a1').last_reachability_check is None
    assert celery_tasks_module.r.get(asset_recheck_lock_key('a1')) is None


@pytest.mark.django_db
def test_recheck_runs_when_no_lock_held(
    eager_celery_recheck: None,
) -> None:
    """No lock held → SETNX succeeds → probe runs."""
    _make_recheck_asset(is_reachable=False)
    with mock.patch(
        'anthias_server.celery_tasks.url_fails', return_value=False
    ):
        revalidate_asset_url.apply(args=('a1',))
    assert Asset.objects.get(asset_id='a1').is_reachable


# ---------------------------------------------------------------------------
# probe_video_duration — async ffprobe path used by the HTML upload view


@pytest.mark.django_db
def test_probe_video_duration_writes_back_real_duration() -> None:
    """Happy path: ffprobe returns a length, the row gets it and is
    flipped out of is_processing."""
    from datetime import timedelta

    Asset.objects.create(
        asset_id='vid-1',
        name='clip.mp4',
        uri='/data/anthias_assets/probe-fixture.mp4',
        mimetype='video',
        duration=10,
        is_enabled=True,
        is_processing=True,
        play_order=0,
    )
    with mock.patch(
        'anthias_server.celery_tasks.get_video_duration',
        return_value=timedelta(seconds=42),
    ):
        probe_video_duration('vid-1')
    a = Asset.objects.get(asset_id='vid-1')
    assert a.duration == 42
    assert a.is_processing is False


@pytest.mark.django_db
def test_probe_video_duration_clears_processing_when_ffprobe_unavailable() -> (
    None
):
    """ffprobe missing → keep the seeded duration, still clear the
    processing flag so the row leaves the placeholder state."""
    Asset.objects.create(
        asset_id='vid-2',
        name='clip.mp4',
        uri='/data/anthias_assets/probe-fixture.mp4',
        mimetype='video',
        duration=10,
        is_enabled=True,
        is_processing=True,
        play_order=0,
    )
    with mock.patch(
        'anthias_server.celery_tasks.get_video_duration', return_value=None
    ):
        probe_video_duration('vid-2')
    a = Asset.objects.get(asset_id='vid-2')
    assert a.duration == 10
    assert a.is_processing is False


@pytest.mark.django_db
def test_probe_video_duration_no_op_for_unknown_asset() -> None:
    """Stale asset_id (deleted between enqueue and run) — task must not
    crash. Nothing to assert beyond a clean return."""
    probe_video_duration('does-not-exist')


# ---------------------------------------------------------------------------
# download_youtube_asset
# ---------------------------------------------------------------------------


def _make_youtube_asset(asset_id: str = 'yt-1') -> Asset:
    """A row in the state the serializer / frontend create leaves
    behind: mimetype=video, is_processing=True, uri pointing at the
    local destination path, duration=0 placeholder."""
    return Asset.objects.create(
        asset_id=asset_id,
        name='https://www.youtube.com/watch?v=abc',
        uri=path.join(settings['assetdir'], f'{asset_id}.mp4'),
        mimetype='video',
        duration=0,
        is_enabled=True,
        is_processing=True,
        play_order=0,
    )


@pytest.fixture
def fake_youtube_dl() -> Iterator[mock.MagicMock]:
    """Patch ``yt_dlp.YoutubeDL`` with a context-manager-shaped mock.

    The task does ``with YoutubeDL(opts) as ydl: ydl.extract_info(...)``,
    so the fake has to support both __enter__ / __exit__ and
    extract_info. Tests configure the return value (or side_effect) of
    extract_info per case.
    """
    fake_cls = mock.MagicMock(name='YoutubeDL')
    fake_inst = mock.MagicMock(name='YoutubeDL_inst')
    fake_cls.return_value.__enter__.return_value = fake_inst
    fake_cls.return_value.__exit__.return_value = False
    with mock.patch.dict('sys.modules'):
        # yt_dlp is lazy-imported inside the task; mock at module load.
        import sys
        import types

        fake_module = types.ModuleType('yt_dlp')
        # setattr keeps mypy happy on a dynamically-created ModuleType
        # (a static `module.attr = ...` assignment is `attr-defined`
        # under --strict).
        setattr(fake_module, 'YoutubeDL', fake_cls)
        utils_mod = types.ModuleType('yt_dlp.utils')

        class FakeDownloadError(Exception):
            pass

        setattr(utils_mod, 'DownloadError', FakeDownloadError)
        sys.modules['yt_dlp'] = fake_module
        sys.modules['yt_dlp.utils'] = utils_mod
        # Exposing the inst lets the test reach `.extract_info` to set
        # return_value / side_effect; the class itself is also handy
        # for assertions about ydl_opts.
        fake_inst._download_error = FakeDownloadError
        fake_inst._cls = fake_cls
        yield fake_inst


@pytest.mark.django_db
def test_download_youtube_asset_success_chains_into_normalize_video(
    fake_youtube_dl: mock.MagicMock,
) -> None:
    """Happy path: extract_info returns populated info; the YouTube
    task writes title + duration + ``metadata['source']`` /
    ``metadata['source_url']``, fires the browser-side notify hop,
    and dispatches ``normalize_video_asset`` so the per-board codec
    grid runs on the downloaded file. ``is_processing`` deliberately
    stays True — normalize clears it once its probe + (optional)
    transcode finishes, giving the operator a single Processing →
    Done state transition rather than the previous flicker."""
    _make_youtube_asset()
    fake_youtube_dl.extract_info.return_value = {
        'title': 'Never Gonna Give You Up',
        'duration': 213,
    }
    fake_redis = mock.MagicMock()
    with (
        mock.patch(
            'anthias_common.utils.connect_to_redis', return_value=fake_redis
        ),
        mock.patch(
            'anthias_server.app.consumers.notify_asset_update'
        ) as mock_notify,
        mock.patch(
            'anthias_server.processing.dispatch_normalize_video'
        ) as mock_dispatch_normalize,
    ):
        download_youtube_asset('yt-1', 'https://www.youtube.com/watch?v=abc')

    a = Asset.objects.get(asset_id='yt-1')
    assert a.name == 'Never Gonna Give You Up'
    assert a.duration == 213
    # Row stays in-flight until normalize finishes.
    assert a.is_processing is True
    # YouTube origin recorded so an operator can tell at a glance
    # where the row came from, and the original URL is recoverable
    # after ``name`` is overwritten with the resolved title.
    assert a.metadata['source'] == 'youtube'
    assert a.metadata['source_url'] == 'https://www.youtube.com/watch?v=abc'

    # Browser-side notify fires so the dashboard picks up the title.
    mock_notify.assert_called_once_with('yt-1')
    # Viewer reload publish does NOT — the row is still
    # is_processing=True, so the on-device viewer would just reload
    # to a row it can't display anyway. The chained
    # normalize_video_asset publishes the reload once is_processing
    # clears.
    fake_redis.publish.assert_not_called()
    mock_dispatch_normalize.assert_called_once_with('yt-1')


@pytest.mark.django_db
def test_download_youtube_asset_writes_to_persisted_uri(
    fake_youtube_dl: mock.MagicMock,
) -> None:
    """The output path passed to yt-dlp must be the row's persisted
    uri, not a fresh recomputation from settings['assetdir']. If
    assetdir was changed between the create call and task pickup
    (operator edited ~/.anthias/anthias.conf), recomputing here
    would write the file to the new path while the row still
    points at the old, and the viewer would see a missing file."""
    Asset.objects.create(
        asset_id='yt-uri',
        name='https://youtu.be/abc',
        # Pin the row to a non-default path: the task must trust this
        # value rather than rebuilding it from settings.
        uri='/custom/assetdir/yt-uri.mp4',
        mimetype='video',
        duration=0,
        is_enabled=True,
        is_processing=True,
        play_order=0,
    )
    fake_youtube_dl.extract_info.return_value = {'title': 't', 'duration': 5}
    with (
        mock.patch('anthias_server.app.consumers.notify_asset_update'),
        mock.patch('anthias_server.processing.dispatch_normalize_video'),
    ):
        download_youtube_asset('yt-uri', 'https://youtu.be/abc')
    # ydl_opts is the first positional arg to the YoutubeDL ctor.
    fake_cls = fake_youtube_dl._cls
    fake_cls.assert_called_once()
    ydl_opts = fake_cls.call_args.args[0]
    assert ydl_opts['outtmpl'] == '/custom/assetdir/yt-uri.mp4'
    # The row already had a uri; the task must not stomp on it.
    assert Asset.objects.get(asset_id='yt-uri').uri == (
        '/custom/assetdir/yt-uri.mp4'
    )


@pytest.mark.django_db
def test_download_youtube_asset_backfills_uri_when_row_uri_missing(
    fake_youtube_dl: mock.MagicMock,
) -> None:
    """Defensive: if the row landed without a uri (e.g. a custom
    caller skipped the serializer's path-stamping), the task uses
    the recomputed destination to download AND backfills the row
    so the viewer + API can find the file. Without the backfill
    the file would land at the fallback path while Asset.uri stays
    empty, leaving the asset orphaned from the operator's view."""
    Asset.objects.create(
        asset_id='yt-empty',
        name='https://youtu.be/abc',
        uri='',
        mimetype='video',
        duration=0,
        is_enabled=True,
        is_processing=True,
        play_order=0,
    )
    fake_youtube_dl.extract_info.return_value = {'title': 't', 'duration': 5}
    with (
        mock.patch('anthias_server.app.consumers.notify_asset_update'),
        mock.patch('anthias_server.processing.dispatch_normalize_video'),
    ):
        download_youtube_asset('yt-empty', 'https://youtu.be/abc')

    a = Asset.objects.get(asset_id='yt-empty')
    expected = path.join(settings['assetdir'], 'yt-empty.mp4')
    assert a.uri == expected
    # And the actual yt-dlp invocation matched that same path.
    fake_cls = fake_youtube_dl._cls
    assert fake_cls.call_args.args[0]['outtmpl'] == expected


@pytest.mark.django_db
def test_download_youtube_asset_floors_subsecond_duration_to_one(
    fake_youtube_dl: mock.MagicMock,
) -> None:
    """A sub-second clip must not slot a 0s rotation entry."""
    _make_youtube_asset()
    fake_youtube_dl.extract_info.return_value = {
        'title': 't',
        'duration': 0.4,
    }
    with (
        mock.patch('anthias_server.app.consumers.notify_asset_update'),
        mock.patch('anthias_server.processing.dispatch_normalize_video'),
    ):
        download_youtube_asset('yt-1', 'https://youtu.be/abc')
    assert Asset.objects.get(asset_id='yt-1').duration == 1


@pytest.mark.django_db
def test_download_youtube_asset_handles_missing_duration(
    fake_youtube_dl: mock.MagicMock,
) -> None:
    """Live streams / radio uploads omit duration. Persist what we
    have (title) without overwriting the placeholder duration. Row
    stays ``is_processing=True`` so normalize_video can finalise."""
    _make_youtube_asset()
    fake_youtube_dl.extract_info.return_value = {
        'title': 'Live now',
        'duration': None,
    }
    with (
        mock.patch('anthias_server.app.consumers.notify_asset_update'),
        mock.patch('anthias_server.processing.dispatch_normalize_video'),
    ):
        download_youtube_asset('yt-1', 'https://www.youtube.com/watch?v=abc')
    a = Asset.objects.get(asset_id='yt-1')
    assert a.name == 'Live now'
    # Placeholder seeded by the serializer; not overwritten.
    assert a.duration == 0
    # is_processing left True for the chained normalize_video pass.
    assert a.is_processing is True


@pytest.mark.django_db
def test_download_youtube_asset_no_op_for_missing_row(
    fake_youtube_dl: mock.MagicMock,
) -> None:
    """Row deleted between dispatch and pickup — task returns cleanly
    without invoking yt-dlp at all."""
    download_youtube_asset(
        'does-not-exist', 'https://www.youtube.com/watch?v=abc'
    )
    fake_youtube_dl.extract_info.assert_not_called()


@pytest.mark.django_db
def test_download_youtube_asset_no_op_when_row_already_finalized(
    fake_youtube_dl: mock.MagicMock,
) -> None:
    """A duplicate task firing for a row whose first invocation
    already cleared is_processing must not re-download or stomp on
    operator-edited state."""
    Asset.objects.create(
        asset_id='yt-2',
        name='Some title',
        uri=path.join(settings['assetdir'], 'yt-2.mp4'),
        mimetype='video',
        duration=120,
        is_enabled=True,
        is_processing=False,
        play_order=0,
    )
    download_youtube_asset('yt-2', 'https://youtu.be/abc')
    fake_youtube_dl.extract_info.assert_not_called()


@pytest.mark.django_db
def test_download_youtube_asset_failure_propagates_for_on_failure(
    fake_youtube_dl: mock.MagicMock,
) -> None:
    """yt-dlp DownloadError is permanent — the task re-raises so
    Celery's on_failure path runs (which clears is_processing)."""
    _make_youtube_asset()
    DownloadError = fake_youtube_dl._download_error  # noqa: N806
    fake_youtube_dl.extract_info.side_effect = DownloadError('404')
    with pytest.raises(DownloadError):
        download_youtube_asset('yt-1', 'https://youtu.be/dead')


@pytest.mark.django_db
def test_download_youtube_asset_on_failure_writes_error_metadata() -> None:
    """When Celery declares the task failed, is_processing must
    flip back to False AND ``metadata.error_message`` must carry the
    exception type + message so the asset table renders the "Failed"
    pill (with the message on hover) — same operator-visible
    contract as a HEIC/video normalisation failure. Without this
    unification, a yt-dlp DownloadError would clear the Processing
    pill but leave no on-row diagnostic."""
    _make_youtube_asset()
    with (
        mock.patch(
            'anthias_server.app.consumers.notify_asset_update'
        ) as mock_notify,
    ):
        download_youtube_asset.on_failure(
            RuntimeError('404 Not Found'),
            task_id='t-1',
            args=('yt-1',),
            kwargs={},
            einfo=None,
        )
    a = Asset.objects.get(asset_id='yt-1')
    assert a.is_processing is False
    # Same shape as _NormalizeAssetTask.on_failure: ExceptionType: msg.
    assert 'RuntimeError' in a.metadata['error_message']
    assert '404 Not Found' in a.metadata['error_message']
    mock_notify.assert_called_once_with('yt-1')


# ---------------------------------------------------------------------------
# download_remote_video_asset — generic http(s) single-file video URLs
# ---------------------------------------------------------------------------


from anthias_server.celery_tasks import (  # noqa: E402
    RemoteVideoDownloadError,
    download_remote_video_asset,
)


def _make_remote_video_asset(
    asset_dir: str,
    asset_id: str = 'rv-1',
    ext: str = '.mp4',
    is_processing: bool = True,
) -> Asset:
    """A row in the state the serializer leaves behind for an
    http(s) video URL: mimetype='video', is_processing=True, uri
    pointing at the eventual local destination."""
    return Asset.objects.create(
        asset_id=asset_id,
        name='https://example.com/clip' + ext,
        uri=path.join(asset_dir, f'{asset_id}{ext}'),
        mimetype='video',
        duration=0,
        is_enabled=True,
        is_processing=is_processing,
        play_order=0,
    )


def _fake_response(
    body: bytes = b'fake mp4 bytes',
    content_type: str = 'video/mp4',
    status_code: int = 200,
    chunk_size: int = 4,
) -> mock.MagicMock:
    """Shape a fake ``requests.get`` response: context-manager-shaped,
    streams ``body`` in fixed-size chunks via ``iter_content``."""
    resp = mock.MagicMock()
    resp.status_code = status_code
    resp.headers = {'Content-Type': content_type}

    def _iter_content(chunk_size: int) -> Iterator[bytes]:
        for i in range(0, len(body), chunk_size):
            yield body[i : i + chunk_size]

    resp.iter_content = _iter_content
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


@pytest.fixture
def remote_video_asset_dir() -> Iterator[str]:
    """Per-test temp directory pinned as settings['assetdir'] so the
    download task writes inside it and we can inspect the on-disk
    result without depending on the host filesystem."""
    Asset.objects.all().delete()
    tmp = tempfile.TemporaryDirectory()
    original = settings['assetdir']
    settings['assetdir'] = tmp.name
    try:
        yield tmp.name
    finally:
        settings['assetdir'] = original
        tmp.cleanup()
        Asset.objects.all().delete()


@pytest.mark.django_db
def test_download_remote_video_asset_success_chains_into_normalize(
    remote_video_asset_dir: str,
) -> None:
    """Happy path: requests streams a small payload to the row's
    persisted ``uri``, metadata gets stamped with ``source='remote_url'``
    and ``source_url``, the dashboard notify fires, and
    ``normalize_video_asset`` is dispatched so the per-board codec
    gate runs. ``is_processing`` stays True across the chain — the
    chained normalize_video clears it once ffprobe finishes."""
    _make_remote_video_asset(remote_video_asset_dir)
    payload = b'mp4-bytes' * 100
    with (
        mock.patch('requests.get', return_value=_fake_response(body=payload)),
        mock.patch(
            'anthias_server.processing.dispatch_normalize_video'
        ) as mock_dispatch_normalize,
        mock.patch(
            'anthias_server.app.consumers.notify_asset_update'
        ) as mock_notify,
    ):
        download_remote_video_asset('rv-1', 'https://example.com/clip.mp4')

    a = Asset.objects.get(asset_id='rv-1')
    # File landed at the persisted destination, .part is gone.
    dest = path.join(remote_video_asset_dir, 'rv-1.mp4')
    assert path.isfile(dest)
    assert not path.exists(f'{dest}.part')
    with open(dest, 'rb') as fh:
        assert fh.read() == payload
    # Origin URL is recoverable after future row edits.
    assert a.metadata['source'] == 'remote_url'
    assert a.metadata['source_url'] == 'https://example.com/clip.mp4'
    # Row stays in-flight until normalize clears the flag — matches
    # the YouTube lifecycle for a single Processing → Done arc.
    assert a.is_processing is True
    mock_dispatch_normalize.assert_called_once_with('rv-1')
    mock_notify.assert_called_once_with('rv-1')


@pytest.mark.django_db
def test_download_remote_video_asset_size_cap_aborts(
    remote_video_asset_dir: str,
) -> None:
    """A payload that streams past ``REMOTE_VIDEO_MAX_BYTES`` raises
    inside the task body. The ``.part`` file must be cleaned up so a
    multi-GB partial download doesn't linger on the SD card waiting
    for the cleanup() sweep an hour later.

    Monkey-patch the cap down to 100 bytes so the test allocates a
    tiny payload — actually writing 5 GiB of fake bytes would blow
    out the test runner's tmpfs / disk quota.
    """
    _make_remote_video_asset(remote_video_asset_dir)
    fake_resp = _fake_response(body=b'x' * 200, chunk_size=32)
    with (
        mock.patch.object(celery_tasks_module, 'REMOTE_VIDEO_MAX_BYTES', 100),
        mock.patch('requests.get', return_value=fake_resp),
        mock.patch(
            'anthias_server.processing.dispatch_normalize_video'
        ) as mock_dispatch,
        mock.patch('anthias_server.app.consumers.notify_asset_update'),
    ):
        with pytest.raises(RemoteVideoDownloadError, match='size cap'):
            download_remote_video_asset('rv-1', 'https://example.com/huge.mp4')
    dest = path.join(remote_video_asset_dir, 'rv-1.mp4')
    assert not path.exists(dest)
    assert not path.exists(f'{dest}.part')
    mock_dispatch.assert_not_called()


@pytest.mark.django_db
def test_download_remote_video_asset_http_404_propagates_for_on_failure(
    remote_video_asset_dir: str,
) -> None:
    """A non-2xx response raises ``RemoteVideoDownloadError``. Celery
    routes the exception through ``_DownloadRemoteVideoTask.on_failure``
    which clears ``is_processing`` and writes
    ``metadata.error_message`` — same shape as YouTube's permanent
    failures."""
    _make_remote_video_asset(remote_video_asset_dir)
    with (
        mock.patch(
            'requests.get',
            return_value=_fake_response(status_code=404, body=b''),
        ),
        mock.patch('anthias_server.processing.dispatch_normalize_video'),
        mock.patch('anthias_server.app.consumers.notify_asset_update'),
    ):
        with pytest.raises(RemoteVideoDownloadError, match='HTTP 404'):
            download_remote_video_asset(
                'rv-1', 'https://example.com/missing.mp4'
            )


@pytest.mark.django_db
def test_download_remote_video_asset_manifest_content_type_aborts(
    remote_video_asset_dir: str,
) -> None:
    """Defence in depth: even if the serializer's HEAD probe said the
    URL was a single file, an origin that serves an HLS manifest on
    GET must be rejected. Storing the playlist as the asset would
    leave the viewer pointing at a file that references segments it
    can't fetch."""
    _make_remote_video_asset(remote_video_asset_dir)
    with (
        mock.patch(
            'requests.get',
            return_value=_fake_response(
                content_type='application/vnd.apple.mpegurl',
                body=b'#EXTM3U\n',
            ),
        ),
        mock.patch('anthias_server.processing.dispatch_normalize_video'),
        mock.patch('anthias_server.app.consumers.notify_asset_update'),
    ):
        with pytest.raises(RemoteVideoDownloadError, match='manifest'):
            download_remote_video_asset('rv-1', 'https://example.com/sneaky')


@pytest.mark.django_db
def test_download_remote_video_asset_wrong_content_type_aborts(
    remote_video_asset_dir: str,
) -> None:
    """``Content-Type: text/html`` on the GET response — most likely
    a 200-OK error page from a misbehaving origin. Reject rather than
    store the HTML as the asset."""
    _make_remote_video_asset(remote_video_asset_dir)
    with (
        mock.patch(
            'requests.get',
            return_value=_fake_response(
                content_type='text/html', body=b'<html>not here</html>'
            ),
        ),
        mock.patch('anthias_server.processing.dispatch_normalize_video'),
        mock.patch('anthias_server.app.consumers.notify_asset_update'),
    ):
        with pytest.raises(RemoteVideoDownloadError, match='Content-Type'):
            download_remote_video_asset('rv-1', 'https://example.com/error')


@pytest.mark.django_db
def test_download_remote_video_asset_accepts_octet_stream(
    remote_video_asset_dir: str,
) -> None:
    """Some CDNs serve video files as ``application/octet-stream``.
    Accept it — ffprobe in the chained normalize step is the real
    arbiter of container/codec, not the HTTP Content-Type."""
    _make_remote_video_asset(remote_video_asset_dir)
    with (
        mock.patch(
            'requests.get',
            return_value=_fake_response(
                content_type='application/octet-stream',
                body=b'mp4-bytes' * 50,
            ),
        ),
        mock.patch(
            'anthias_server.processing.dispatch_normalize_video'
        ) as mock_dispatch,
        mock.patch('anthias_server.app.consumers.notify_asset_update'),
    ):
        download_remote_video_asset('rv-1', 'https://example.com/clip.mp4')
    dest = path.join(remote_video_asset_dir, 'rv-1.mp4')
    assert path.isfile(dest)
    mock_dispatch.assert_called_once_with('rv-1')


@pytest.mark.django_db
def test_download_remote_video_asset_zero_bytes_aborts(
    remote_video_asset_dir: str,
) -> None:
    """A 200 OK with an empty body almost certainly means the origin
    is broken; storing an empty file would just queue a row that the
    viewer would refuse anyway. Surface it as a clear failure on the
    operator's row."""
    _make_remote_video_asset(remote_video_asset_dir)
    with (
        mock.patch('requests.get', return_value=_fake_response(body=b'')),
        mock.patch('anthias_server.processing.dispatch_normalize_video'),
        mock.patch('anthias_server.app.consumers.notify_asset_update'),
    ):
        with pytest.raises(RemoteVideoDownloadError, match='zero bytes'):
            download_remote_video_asset(
                'rv-1', 'https://example.com/empty.mp4'
            )
    dest = path.join(remote_video_asset_dir, 'rv-1.mp4')
    assert not path.exists(dest)
    assert not path.exists(f'{dest}.part')


@pytest.mark.django_db
def test_download_remote_video_asset_no_op_for_missing_row(
    remote_video_asset_dir: str,
) -> None:
    """Row deleted between dispatch and pickup — task returns cleanly
    without hitting the network."""
    with mock.patch('requests.get') as fake_get:
        download_remote_video_asset(
            'does-not-exist', 'https://example.com/x.mp4'
        )
    fake_get.assert_not_called()


@pytest.mark.django_db
def test_download_remote_video_asset_no_op_when_row_already_finalized(
    remote_video_asset_dir: str,
) -> None:
    """A duplicate task firing for a row whose first invocation already
    cleared is_processing must not re-download or stomp on
    operator-edited state."""
    _make_remote_video_asset(remote_video_asset_dir, is_processing=False)
    with mock.patch('requests.get') as fake_get:
        download_remote_video_asset('rv-1', 'https://example.com/x.mp4')
    fake_get.assert_not_called()


@pytest.mark.django_db
def test_download_remote_video_asset_backfills_uri_when_row_uri_missing(
    remote_video_asset_dir: str,
) -> None:
    """Defensive: if the row landed without a uri (e.g. a custom caller
    skipped the serializer's path-stamping), the task uses the
    recomputed destination AND backfills the row so the viewer + API
    can find the file."""
    Asset.objects.create(
        asset_id='rv-empty',
        name='https://example.com/x.mp4',
        uri='',
        mimetype='video',
        duration=0,
        is_enabled=True,
        is_processing=True,
        play_order=0,
    )
    with (
        mock.patch(
            'requests.get',
            return_value=_fake_response(body=b'mp4-bytes'),
        ),
        mock.patch('anthias_server.processing.dispatch_normalize_video'),
        mock.patch('anthias_server.app.consumers.notify_asset_update'),
    ):
        download_remote_video_asset('rv-empty', 'https://example.com/x.mp4')
    a = Asset.objects.get(asset_id='rv-empty')
    expected = path.join(remote_video_asset_dir, 'rv-empty.mp4')
    assert a.uri == expected
    assert path.isfile(expected)


@pytest.mark.django_db
def test_download_remote_video_asset_on_failure_writes_error_metadata(
    remote_video_asset_dir: str,
) -> None:
    """When Celery declares the task failed, is_processing must flip
    back to False AND ``metadata.error_message`` must carry the
    exception class + message so the asset table renders the "Failed"
    pill — same operator-visible contract as YouTube / normalize
    failures."""
    _make_remote_video_asset(remote_video_asset_dir)
    with mock.patch(
        'anthias_server.app.consumers.notify_asset_update'
    ) as mock_notify:
        download_remote_video_asset.on_failure(
            RuntimeError('connection refused'),
            task_id='t-1',
            args=('rv-1',),
            kwargs={},
            einfo=None,
        )
    a = Asset.objects.get(asset_id='rv-1')
    assert a.is_processing is False
    assert 'RuntimeError' in a.metadata['error_message']
    assert 'connection refused' in a.metadata['error_message']
    mock_notify.assert_called_once_with('rv-1')


# ---------------------------------------------------------------------------
# reconcile_stuck_processing (periodic recovery for is_processing=True
# rows that never finished — GH #2870 second-line defence)
# ---------------------------------------------------------------------------


from datetime import timedelta  # noqa: E402

from django.utils import timezone  # noqa: E402

from anthias_server.celery_tasks import (  # noqa: E402
    RECONCILE_STUCK_LOCK_KEY,
    RECONCILE_STUCK_THRESHOLD_S,
    reconcile_stuck_processing,
)


def _make_stuck_asset(
    *,
    asset_id: str = 'stuck-1',
    mimetype: str = 'video',
    is_processing: bool = True,
    processing_started_at: object = None,
    extra_metadata: dict[str, object] | None = None,
) -> Asset:
    """Build a row in the state the reconciler is meant to find.

    The metadata key is the load-bearing field — every age-based
    decision the reconciler makes is driven by it.
    """
    metadata: dict[str, object] = {}
    if extra_metadata:
        metadata.update(extra_metadata)
    if processing_started_at is not None:
        metadata['processing_started_at'] = processing_started_at
    return Asset.objects.create(
        asset_id=asset_id,
        name=asset_id,
        uri=f'/data/anthias_assets/{asset_id}.mp4',
        mimetype=mimetype,
        duration=10,
        is_enabled=True,
        is_processing=is_processing,
        metadata=metadata,
    )


@pytest.fixture
def eager_celery_reconcile() -> None:
    celeryapp.conf.update(
        CELERY_ALWAYS_EAGER=True,
        CELERY_RESULT_BACKEND='',
        CELERY_BROKER_URL='',
    )
    Asset.objects.all().delete()


@pytest.mark.django_db
def test_reconcile_skips_rows_within_grace_window(
    eager_celery_reconcile: None,
) -> None:
    """A row whose ``processing_started_at`` is younger than the
    threshold belongs to an in-flight task — leave it alone."""
    young = (
        timezone.now() - timedelta(seconds=RECONCILE_STUCK_THRESHOLD_S - 60)
    ).isoformat()
    _make_stuck_asset(processing_started_at=young, mimetype='video')

    with (
        mock.patch(
            'anthias_server.processing.dispatch_normalize_video'
        ) as mock_video,
        mock.patch(
            'anthias_server.processing.dispatch_normalize_image'
        ) as mock_image,
    ):
        reconcile_stuck_processing.apply()

    mock_video.assert_not_called()
    mock_image.assert_not_called()
    # Row left as-is — still flagged so the actual task can finish.
    assert Asset.objects.get(asset_id='stuck-1').is_processing is True


@pytest.mark.django_db
def test_reconcile_redispatches_stuck_video(
    eager_celery_reconcile: None,
) -> None:
    """An ``is_processing=True`` video row older than the threshold
    is re-dispatched through ``dispatch_normalize_video`` — recovers
    rows whose worker crashed between enqueue and pickup."""
    old = (
        timezone.now() - timedelta(seconds=RECONCILE_STUCK_THRESHOLD_S + 60)
    ).isoformat()
    _make_stuck_asset(processing_started_at=old, mimetype='video')

    with mock.patch(
        'anthias_server.processing.dispatch_normalize_video'
    ) as mock_video:
        reconcile_stuck_processing.apply()

    mock_video.assert_called_once_with('stuck-1')


@pytest.mark.django_db
def test_reconcile_redispatches_stuck_image(
    eager_celery_reconcile: None,
) -> None:
    """Same recovery path applies to image-mimetype rows."""
    old = (
        timezone.now() - timedelta(seconds=RECONCILE_STUCK_THRESHOLD_S + 60)
    ).isoformat()
    _make_stuck_asset(
        asset_id='stuck-img',
        processing_started_at=old,
        mimetype='image',
    )

    with mock.patch(
        'anthias_server.processing.dispatch_normalize_image'
    ) as mock_image:
        reconcile_stuck_processing.apply()

    mock_image.assert_called_once_with('stuck-img')


@pytest.mark.django_db
def test_reconcile_stamps_unstamped_row_on_first_sweep(
    eager_celery_reconcile: None,
) -> None:
    """Legacy / backup-restored rows have no
    ``metadata.processing_started_at``. The first sweep stamps it so
    the next sweep can apply the age threshold uniformly — gives the
    full grace window in case a task is actually still running.
    Crucially the first sweep must NOT re-dispatch (otherwise a
    still-in-flight task gets a duplicate)."""
    _make_stuck_asset(
        asset_id='unstamped',
        processing_started_at=None,
        mimetype='video',
    )

    with mock.patch(
        'anthias_server.processing.dispatch_normalize_video'
    ) as mock_video:
        reconcile_stuck_processing.apply()

    mock_video.assert_not_called()
    a = Asset.objects.get(asset_id='unstamped')
    assert a.is_processing is True
    assert 'processing_started_at' in a.metadata


@pytest.mark.django_db
def test_reconcile_clears_flag_on_unknown_mimetype(
    eager_celery_reconcile: None,
) -> None:
    """A row with a mimetype neither image nor video that's stuck
    past the threshold has no clear re-dispatch path. Clear the
    flag and surface an error so the operator can interact with it."""
    old = (
        timezone.now() - timedelta(seconds=RECONCILE_STUCK_THRESHOLD_S + 60)
    ).isoformat()
    _make_stuck_asset(
        asset_id='weird',
        processing_started_at=old,
        mimetype='webpage',
    )

    with (
        mock.patch(
            'anthias_server.processing.dispatch_normalize_video'
        ) as mock_video,
        mock.patch(
            'anthias_server.processing.dispatch_normalize_image'
        ) as mock_image,
        mock.patch('anthias_server.app.consumers.notify_asset_update'),
    ):
        reconcile_stuck_processing.apply()

    mock_video.assert_not_called()
    mock_image.assert_not_called()
    a = Asset.objects.get(asset_id='weird')
    assert a.is_processing is False
    assert 'reconciler' in a.metadata.get('error_message', '').lower()


@pytest.mark.django_db
def test_reconcile_treats_malformed_timestamp_as_unstamped(
    eager_celery_reconcile: None,
) -> None:
    """A non-parseable ``processing_started_at`` (e.g. from a
    hand-edited backup) must follow the same "stamp on first sight"
    recovery path — never raise during sweep."""
    _make_stuck_asset(
        asset_id='bad-ts',
        processing_started_at='not a timestamp',
        mimetype='video',
    )

    with mock.patch(
        'anthias_server.processing.dispatch_normalize_video'
    ) as mock_video:
        reconcile_stuck_processing.apply()

    mock_video.assert_not_called()
    a = Asset.objects.get(asset_id='bad-ts')
    # Fresh stamp written; existing extra metadata preserved.
    parsed = a.metadata['processing_started_at']
    assert parsed != 'not a timestamp'
    # The new value is a valid ISO-8601 string.
    from datetime import datetime

    datetime.fromisoformat(parsed)


@pytest.mark.django_db
def test_reconcile_skips_rows_not_processing(
    eager_celery_reconcile: None,
) -> None:
    """``is_processing=False`` rows aren't the reconciler's business,
    regardless of any stale ``processing_started_at`` left in metadata
    from a previous run."""
    old = (
        timezone.now() - timedelta(seconds=RECONCILE_STUCK_THRESHOLD_S + 60)
    ).isoformat()
    _make_stuck_asset(
        asset_id='not-processing',
        is_processing=False,
        processing_started_at=old,
    )

    with mock.patch(
        'anthias_server.processing.dispatch_normalize_video'
    ) as mock_video:
        reconcile_stuck_processing.apply()

    mock_video.assert_not_called()


@pytest.mark.django_db
def test_reconcile_treats_naive_timestamp_as_unstamped(
    eager_celery_reconcile: None,
) -> None:
    """A naive (no tzinfo) ISO-8601 string in
    ``metadata.processing_started_at`` could come from a hand-edited
    backup. Comparing it against the tz-aware ``cutoff`` would raise
    ``TypeError`` and abort the sweep — handle it the same way as a
    malformed string: stamp on first sight, re-evaluate on the next
    sweep. Guards against a single bad row breaking the whole sweep
    for everyone else."""
    naive = '2020-01-01T00:00:00'  # well past threshold, but no tz
    _make_stuck_asset(
        asset_id='naive-ts',
        processing_started_at=naive,
        mimetype='video',
    )

    with mock.patch(
        'anthias_server.processing.dispatch_normalize_video'
    ) as mock_video:
        reconcile_stuck_processing.apply()

    mock_video.assert_not_called()
    a = Asset.objects.get(asset_id='naive-ts')
    # The naive value was overwritten with a fresh tz-aware stamp.
    new_stamp = a.metadata['processing_started_at']
    assert new_stamp != naive
    from datetime import datetime

    assert datetime.fromisoformat(new_stamp).tzinfo is not None


@pytest.mark.django_db
def test_reconcile_lock_prevents_overlap(
    eager_celery_reconcile: None,
) -> None:
    """A second beat tick that fires while the reconciler is running
    must be a no-op. Mirrors ``revalidate_asset_urls`` behaviour: only
    one sweep at a time re-dispatches a given stuck row, regardless of
    how many workers run embedded beat."""
    old = (
        timezone.now() - timedelta(seconds=RECONCILE_STUCK_THRESHOLD_S + 60)
    ).isoformat()
    _make_stuck_asset(processing_started_at=old, mimetype='video')

    # Pre-acquire the lock to simulate a sweep already in flight.
    celery_tasks_module.r.set(RECONCILE_STUCK_LOCK_KEY, 'someone-else')

    with mock.patch(
        'anthias_server.processing.dispatch_normalize_video'
    ) as mock_video:
        reconcile_stuck_processing.apply()

    # Saw the lock and exited without re-dispatching.
    mock_video.assert_not_called()
    # Critically did NOT clobber the existing holder's token.
    assert (
        celery_tasks_module.r.get(RECONCILE_STUCK_LOCK_KEY) == 'someone-else'
    )


@pytest.mark.django_db
def test_reconcile_lock_released_after_clean_run(
    eager_celery_reconcile: None,
) -> None:
    """The ``finally`` block must release our token so the next beat
    tick can run."""
    _make_stuck_asset(processing_started_at=None, mimetype='video')

    with mock.patch('anthias_server.processing.dispatch_normalize_video'):
        reconcile_stuck_processing.apply()

    assert celery_tasks_module.r.get(RECONCILE_STUCK_LOCK_KEY) is None
