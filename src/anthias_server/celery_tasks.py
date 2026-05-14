import logging
import os
import secrets
import time
from datetime import datetime, timedelta
from os import getenv, path
from typing import Any

import django
import sh
from celery import Celery, Task
from django.apps import apps as _django_apps
from PIL import UnidentifiedImageError
from tenacity import Retrying, stop_after_attempt, wait_fixed

# ``django.setup()`` is not reentrant — calling it while
# ``apps.populate()`` is still running (e.g. when an ``AppConfig.ready``
# hook imports this module) raises ``RuntimeError: populate() isn't
# reentrant`` and the import dies, taking the caller down silently in
# any try/except chain. ``apps_ready`` flips to ``True`` after Django
# finishes the import phase but *before* the per-app ``ready`` hooks
# run, so the check correctly distinguishes:
#
#   * standalone celery worker process → Django not initialised yet
#     (``apps_ready=False``) → ``setup()`` runs as before;
#   * import from inside an ``AppConfig.ready`` (server process)
#     → Django is mid-populate (``apps_ready=True``) → skip.
if not _django_apps.apps_ready:
    django.setup()

# Place imports that uses Django in this block.

from anthias_server.app.models import Asset  # noqa: E402
from anthias_server.lib import diagnostics  # noqa: E402
from anthias_server.lib.telemetry import send_telemetry  # noqa: E402
from anthias_common.utils import (  # noqa: E402
    connect_to_redis,
    get_video_duration,
    is_balena_app,
    reboot_via_balena_supervisor,
    shutdown_via_balena_supervisor,
    url_fails,
)
from anthias_common.youtube import youtube_destination_path  # noqa: E402
from anthias_server.settings import settings  # noqa: E402


__author__ = 'Screenly, Inc'
__copyright__ = 'Copyright 2012-2026, Screenly, Inc'
__license__ = 'Dual License: GPLv2 and Commercial License'


CELERY_RESULT_BACKEND = getenv(
    'CELERY_RESULT_BACKEND', 'redis://localhost:6379/0'
)
CELERY_BROKER_URL = getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_TASK_RESULT_EXPIRES = timedelta(hours=6)

r = connect_to_redis()
celery = Celery(
    'Anthias Celery Worker',
    backend=CELERY_RESULT_BACKEND,
    broker=CELERY_BROKER_URL,
    result_expires=CELERY_TASK_RESULT_EXPIRES,
)


# Sweep cadence for the asset URL re-validation job. 15 min is short
# enough to catch a stream that's been down for a rotation or two,
# long enough that the sweep cost (one ffprobe per stream + one HEAD
# per HTTP asset) doesn't compound on a large playlist.
ASSET_REVALIDATION_INTERVAL_S = 60 * 15

# Sweep cadence for the stuck-``is_processing`` reconciler. 10 min is
# short enough that an operator sees a hung row recover within one
# UI sit-down. The per-tick cost is bounded by the number of stuck
# rows: one initial SELECT over ``is_processing=True``, then per row
# either a stamp (SELECT + UPDATE inside ``stamp_processing_start``)
# or a normalize re-dispatch (which itself stamps). On a healthy
# fleet the filtered set is usually empty so the tick costs one
# SELECT total; only a backlog of stuck rows scales the work up.
RECONCILE_STUCK_INTERVAL_S = 60 * 10

# Age threshold for considering a row stuck. Has to be *longer* than
# the longest reasonable Celery task: ``NORMALIZE_VIDEO_TIME_LIMIT_S``
# is 30 min and ``YOUTUBE_DOWNLOAD_TIME_LIMIT_S`` is 15 min, so 60 min
# is a safe floor. A row past the threshold either had its worker
# time-limit expire (in which case ``on_failure`` should already have
# cleared the flag — and the reconciler only sees rows where it
# didn't, e.g. SIGKILL before on_failure could run) OR was never
# picked up at all (Redis flake during ``.delay()``, worker crashed
# between enqueue and accept, container restart mid-dispatch). The
# threshold doesn't distinguish the two cases — it just says "if a
# row has carried ``is_processing=True`` for over an hour, something
# went wrong, recover it".
RECONCILE_STUCK_THRESHOLD_S = 60 * 60

# Singleton lock for the stuck-row reconciler — same SETNX +
# Lua-compare-and-delete pattern as ``revalidate_asset_urls``. The
# embedded beat scheduler (``celery worker -B``) fires the periodic
# task inside the same worker process; if a future deploy ever runs
# two workers with embedded beat (or a separate ``celery beat``
# instance) this lock keeps the sweep single-flighted across them and
# prevents the same stuck row from being re-dispatched twice in a
# tick.
RECONCILE_STUCK_LOCK_KEY = 'celery:reconcile_stuck_processing:lock'

# Floor on per-asset re-checks. Independent of the sweep interval —
# this gates the on-demand recheck path so a viewer that keeps
# encountering the same unreachable asset can't flood the worker with
# back-to-back ffprobe runs (each up to 15s wall-clock under url_fails).
RECHECK_COOLDOWN_S = 60

# Hard ceiling on how long a single sweep is allowed to run. Set above
# the periodic interval so a sweep that's running long doesn't get
# guillotined while the next beat tick races to start a new one — the
# Redis lock below is the primary overlap guard, the time_limit just
# bounds pathological cases (broken DNS resolver, a stuck ffprobe).
ASSET_REVALIDATION_TIME_LIMIT_S = 60 * 30

# Redis key for the sweep singleton lock. Whoever sets it first runs
# the sweep; later beat ticks observe the key and exit. The TTL matches
# the time_limit so a worker that crashes mid-sweep doesn't lock the
# next sweep out forever.
ASSET_REVALIDATION_LOCK_KEY = 'celery:revalidate_asset_urls:lock'

# Compare-and-delete: only release the lock if the value still matches
# the token we wrote. Prevents a pathological case where sweep A's TTL
# expires while it's still running, sweep B acquires the (now-free)
# lock, and sweep A's ``finally`` block then deletes B's lock and
# allows further overlap. ``redis.eval`` runs the script atomically.
_LOCK_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) "
    'else return 0 end'
)


# Two separate per-asset SETNX gates. The previous design used a
# single timestamp-based cooldown check at both the endpoint and the
# task, which was racy: the timestamp only updates after the probe
# completes, so multiple near-simultaneous callers each read the same
# stale value. SETNX is atomic per-key, but a single shared key
# would conflict — endpoint's lock would block the task it just
# enqueued. Hence two keys with different TTLs and different jobs.

# Short-TTL endpoint debounce. Bounds queue churn from a viewer that
# rotates quickly past the same unreachable asset: only the first
# endpoint call in the window queues a task.
ASSET_RECHECK_QUEUE_DEBOUNCE_S = 5


# Long-TTL task cooldown gate. Prevents concurrent ffprobe / HEAD
# probes for the same asset across Celery workers — only the first
# task to acquire actually runs, others (whether enqueued by the
# endpoint or directly via ``revalidate_asset_url.delay``) no-op.
def asset_recheck_queue_key(asset_id: str) -> str:
    """Endpoint-side queue debounce key (TTL = QUEUE_DEBOUNCE)."""
    return f'recheck:{asset_id}:queue'


def asset_recheck_lock_key(asset_id: str) -> str:
    """Task-side cooldown lock key (TTL = RECHECK_COOLDOWN_S)."""
    return f'recheck:{asset_id}:lock'


@celery.on_after_configure.connect
def setup_periodic_tasks(sender: Any, **kwargs: Any) -> None:
    # Calls cleanup() every hour.
    sender.add_periodic_task(3600, cleanup.s(), name='cleanup')
    sender.add_periodic_task(
        60 * 5, get_display_power.s(), name='display_power'
    )
    # Hourly tick; send_telemetry_task itself enforces a 24h cooldown
    # via Redis, so each device emits at most one GA event per day.
    sender.add_periodic_task(3600, send_telemetry_task.s(), name='telemetry')
    sender.add_periodic_task(
        ASSET_REVALIDATION_INTERVAL_S,
        revalidate_asset_urls.s(),
        name='revalidate_asset_urls',
    )
    sender.add_periodic_task(
        RECONCILE_STUCK_INTERVAL_S,
        reconcile_stuck_processing.s(),
        name='reconcile_stuck_processing',
    )


@celery.task(time_limit=30)
def get_display_power() -> None:
    r.set('display_power', diagnostics.get_display_power())
    r.expire('display_power', 3600)


@celery.task(time_limit=30)
def send_telemetry_task() -> None:
    send_telemetry()


@celery.task
def cleanup() -> None:
    asset_dir = settings['assetdir']
    if not path.isdir(asset_dir):
        return

    # Stale upload remnants: in-progress uploads write to <uuid>.tmp and
    # rename on commit. The 1h mtime guard avoids killing an upload that
    # is still streaming when celery beat fires.
    sh.find(
        asset_dir,
        '-name',
        '*.tmp',
        '-type',
        'f',
        '-mmin',
        '+60',
        '-delete',
    )

    # Orphaned asset files: forum 6636 / GH #2657. Asset rows can be
    # deleted while their file lingers (e.g. URI didn't match assetdir
    # exactly, or the file was renamed by an upgrade). Sweep anything
    # in assetdir that no live Asset row references, with the same 1h
    # guard so a freshly-renamed file (or in-flight yt-dlp sidecar:
    # .part/.ytdl/.info.json) isn't removed before its row is written
    # or its download finishes. Stale sidecars from abandoned downloads
    # fall outside the freshness window and get swept like any other
    # orphan.
    #
    # Resolve URIs through realpath so legacy rows that still reference
    # the pre-rebrand prefix (~/screenly_assets/..., now a symlink to
    # ~/anthias_assets) are recognized as live and their files aren't
    # mistaken for orphans on upgraded installs.
    #
    # The reference set must include the ``.original.<ext>`` sibling
    # for every video row: ``Asset.uri`` points at the playback variant
    # (``<id>.mp4``), and the source is stashed at
    # ``metadata['original_uri']`` (e.g. ``<id>.original.mov``). Without
    # that second source the sweep below would treat every original as
    # an orphan after the 1h mtime cutoff and silently destroy the only
    # path back to the upload bytes.
    asset_dir_real = path.realpath(asset_dir)
    referenced: set[str] = set()

    def _claim(p: str | None) -> None:
        if not p:
            return
        try:
            if path.realpath(path.dirname(p)) == asset_dir_real:
                referenced.add(path.basename(p))
        except OSError:
            return

    for uri, metadata in Asset.objects.exclude(uri__isnull=True).values_list(
        'uri', 'metadata'
    ):
        _claim(uri)
        if isinstance(metadata, dict):
            _claim(metadata.get('original_uri'))
    cutoff = 60 * 60  # match the .tmp guard above
    now = time.time()
    for entry in os.scandir(asset_dir):
        if not entry.is_file():
            continue
        if entry.name in referenced:
            continue
        try:
            if now - entry.stat().st_mtime < cutoff:
                continue
            os.remove(entry.path)
        except OSError as e:
            logging.warning('cleanup: could not remove %s: %s', entry.path, e)


class _ProbeVideoTask(Task):  # type: ignore[type-arg]
    """Custom Task subclass so ``on_failure`` can clear
    ``is_processing`` when retries are exhausted.

    Without this, a row whose probe permanently fails (e.g. ffprobe
    missing on a stripped-down image, or 3 consecutive ffprobe
    timeouts) stays at "Processing" forever and the operator has no
    way to interact with it. Celery calls ``on_failure`` after
    ``max_retries`` is exhausted *or* on any non-autoretry exception
    that escapes the task body.
    """

    def on_failure(
        self,
        exc: BaseException,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        asset_id = args[0] if args else kwargs.get('asset_id')
        if not asset_id:
            return
        try:
            Asset.objects.filter(asset_id=asset_id).update(is_processing=False)
            # Same WS nudge the success path sends so the row drops
            # the "Processing" pill without waiting for the 5s table
            # poll. Operator then has the row in its terminal state
            # and can edit/delete it.
            from anthias_server.app.consumers import notify_asset_update

            notify_asset_update(asset_id)
        except Exception:
            logging.exception(
                'probe_video_duration on_failure cleanup failed for %s',
                asset_id,
            )


@celery.task(
    base=_ProbeVideoTask,
    time_limit=120,
    autoretry_for=(sh.TimeoutException, sh.ErrorReturnCode, OSError),
    retry_backoff=10,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def probe_video_duration(asset_id: str) -> None:
    """Resolve a freshly uploaded video's length out of band.

    The HTML upload view returns the table partial as soon as the bytes
    are written so the operator isn't held up by ffprobe (which can
    take several seconds on a Pi 1/Zero). The asset is marked
    ``is_processing=True`` while this task is queued; once the probe
    completes the duration is written and the flag is cleared, which
    drops the "Processing" pill on the next 5s table poll.

    Retry policy:
      - sh.TimeoutException / sh.ErrorReturnCode / OSError → autoretry
        with exponential backoff (10s / 20s / 40s / cap 300s, max 3
        retries). These cover transient disk pressure, ffprobe timing
        out under load, kernel-level read errors.
      - get_video_duration returning None (ffprobe missing) → not a
        transient error; permanent miss-and-move-on. The row leaves
        is_processing=False so the operator can adjust manually.
      - Any other exception is swallowed (logged) so a programmer
        error in the helper doesn't hammer the worker via retries.
      - Retries exhausted (autoretry_for raise after max_retries) →
        _ProbeVideoTask.on_failure clears is_processing so the row
        doesn't stay stuck at "Processing" indefinitely.
    """
    try:
        asset = Asset.objects.get(asset_id=asset_id)
    except Asset.DoesNotExist:
        return

    if asset.mimetype != 'video' or not asset.uri:
        Asset.objects.filter(asset_id=asset_id).update(is_processing=False)
        return

    # Let TimeoutException / ErrorReturnCode / OSError bubble so the
    # @autoretry_for kwarg picks them up. Other exceptions are bugs;
    # log + leave the row marked done.
    duration: int | None = None
    try:
        td = get_video_duration(asset.uri)
    except (sh.TimeoutException, sh.ErrorReturnCode, OSError):
        raise
    except Exception:
        logging.exception(
            'probe_video_duration: unexpected failure for %s', asset_id
        )
        td = None
    if td is not None:
        duration = max(1, int(td.total_seconds()))

    update: dict[str, Any] = {'is_processing': False}
    if duration is not None:
        update['duration'] = duration
    Asset.objects.filter(asset_id=asset_id).update(**update)

    # Tell the viewer to reload its playlist now that the row is fully
    # materialised — same trigger every other write path uses.
    r.publish('anthias.viewer', 'reload')

    # Push a refresh nudge over the browser-facing WebSocket so the
    # operator sees the row stop "Processing" and pick up its real
    # duration without waiting for the next 5s table poll.
    from anthias_server.app.consumers import notify_asset_update

    notify_asset_update(asset_id)


class _DownloadYoutubeTask(Task):  # type: ignore[type-arg]
    """Custom Task subclass that funnels failures through the same
    metadata-error contract as ``_NormalizeAssetTask``.

    Sharing the failure path means a failed YouTube download lands in
    the same operator-visible state as a failed HEIC conversion or
    failed video transcode: ``is_processing=False`` *and* a populated
    ``metadata.error_message`` that the asset table renders as a
    "Failed" pill (with the message on the hover tooltip). Without
    that unification, a yt-dlp DownloadError would clear the
    Processing pill but leave no on-row diagnostic — the operator
    couldn't tell a fresh download from a 404'd one.

    The previous in-process daemon thread swallowed yt-dlp's exit
    code, so a failed download silently left the row stuck at
    "Processing" with an empty .mp4. Now any uncaught exception
    (DownloadError, ExtractorError, ...) bubbles to celery and lands
    here once retries are exhausted.
    """

    def on_failure(
        self,
        exc: BaseException,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        asset_id = args[0] if args else kwargs.get('asset_id')
        if not asset_id:
            return
        try:
            # Reuse the helpers in anthias_server.processing so the
            # YouTube and normalize pipelines share the exact same
            # "row failed" semantics — single source of truth for the
            # error_message contract instead of two near-duplicate
            # blocks that could drift.
            from anthias_server.processing import (
                _notify,
                _set_processing_error,
            )

            _set_processing_error(asset_id, f'{type(exc).__name__}: {exc}')
            _notify(asset_id)
        except Exception:
            logging.exception(
                'download_youtube_asset on_failure cleanup failed for %s',
                asset_id,
            )


# Bound the wall-clock cost of a single download attempt. 1080p videos
# on a slow connection / Pi 1 can run several minutes; 15 min is a
# generous ceiling that still fails fast enough for the operator to
# notice via the "Processing" pill not clearing.
YOUTUBE_DOWNLOAD_TIME_LIMIT_S = 60 * 15


@celery.task(
    base=_DownloadYoutubeTask,
    time_limit=YOUTUBE_DOWNLOAD_TIME_LIMIT_S,
    # Transient OSError covers IO-level hiccups (disk pressure,
    # connection reset surfacing as ConnectionResetError which is an
    # OSError). yt-dlp's own DownloadError is *not* retried: its
    # common causes (404, 403, age-gate, geo-block, deleted video,
    # signature extraction breakage) are permanent and re-running
    # just burns the worker. Let on_failure clean up.
    autoretry_for=(OSError,),
    retry_backoff=15,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=2,
)
def download_youtube_asset(asset_id: str, uri: str) -> None:
    """Download a YouTube video out of band and finalize the row.

    Replaces the previous ``YoutubeDownloadThread`` (a daemon thread
    in the anthias-server process) which:
      - lost the download on uvicorn restart, leaving rows stuck at
        ``is_processing=True`` indefinitely,
      - ignored yt-dlp's exit code, so failures advertised the asset
        as ready while pointing at an empty file,
      - blocked the API request for two upfront yt-dlp shellouts
        (``-O title`` and ``-j``) before returning.

    The row is created upstream with ``mimetype='video'``,
    ``is_processing=True``, and ``uri`` already pointing at the
    eventual ``<assetdir>/<asset_id>.mp4``. This task overwrites
    ``name`` (with the resolved title), seeds metadata, and then
    *chains into* ``normalize_video_asset`` — leaving the row
    ``is_processing=True`` so the same per-board passthrough /
    transcode pass runs on YouTube downloads as on direct file
    uploads. That matters because yt-dlp's ``format_sort:
    vcodec:h264`` is a *preference*, not a guarantee: when no H.264
    rendition is available yt-dlp falls back to whatever it can get
    (vp9 webm, av1, ...). Without the chain, those downloads would
    land on a pi3 device unplayable. With it, the same codec grid
    that protects file uploads protects YouTube downloads too.

    Uses yt-dlp as a Python library rather than a CLI shellout: a
    single ``extract_info(uri, download=True)`` call returns title +
    duration as Python values, eliminating the previous three-
    shellouts-per-asset pattern (``-O title``, ``-j``, then download)
    plus the brittle ``\\t``-separated ``--print`` parser (YouTube
    titles can contain literal tabs).
    """
    try:
        asset = Asset.objects.get(asset_id=asset_id)
    except Asset.DoesNotExist:
        # Row was deleted between dispatch and pickup. Any partial
        # files left behind get swept by cleanup() after the 1h
        # freshness window — same behaviour as a row deleted
        # mid-download in the old thread path.
        return

    if not asset.is_processing:
        # Row already finalized (e.g. by an earlier invocation) or
        # operator-edited. Don't re-download or clobber its state.
        return

    # Trust the path the upstream caller persisted on the row rather
    # than recomputing from settings['assetdir']: if assetdir was
    # changed between create and task pickup (operator edited
    # ~/.anthias/anthias.conf, settings reloaded), recomputing here
    # would write the file to the new path while the row still
    # points at the old, and the viewer would see a missing file.
    # Falling back to the recomputed path covers the (defensive)
    # case where uri is empty for some reason — same destination as
    # the serializer / frontend create would have produced.
    location = asset.uri or youtube_destination_path(asset_id, settings)

    # Lazy import: yt_dlp pulls in hundreds of extractors at import
    # time. Keeping it inside the task body avoids the cost on
    # worker startup for jobs that don't touch YouTube.
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    ydl_opts = {
        # ``format_sort`` mirrors the previous CLI's `-S
        # vcodec:h264,fps,res:1080,acodec:m4a` — bias toward h264
        # video and m4a audio, keep resolution at 1080p, prefer
        # higher fps. yt-dlp still picks the *best matching* format,
        # falling back to whatever is available if no exact match
        # exists. Strict `format=` filters would reject videos that
        # happen to have only vp9, which we don't want.
        'format_sort': ['vcodec:h264', 'fps', 'res:1080', 'acodec:m4a'],
        # Final filename — yt-dlp writes <location>.part during the
        # download and renames on success. cleanup() recognises
        # .part / .info.json sidecars and skips them inside the 1h
        # freshness window.
        'outtmpl': location,
        # Quiet the worker log: yt-dlp's progress bars and "info"
        # noise drown out everything else under load. Errors still
        # raise DownloadError, which we surface via celery.
        'noprogress': True,
        'quiet': True,
        'no_warnings': True,
        # Don't pull a playlist if the URL happens to be one — we
        # only want a single-file asset row per request. The viewer
        # has no concept of "this row expands into N files".
        'noplaylist': True,
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(uri, download=True)
    except DownloadError:
        # Permanent failure surface — let it bubble to on_failure.
        # autoretry_for excludes DownloadError specifically.
        raise

    if info is None:
        # Should not happen with a successful extract_info, but
        # guard so a future yt-dlp behaviour change doesn't write a
        # row with a stale name and stale duration. on_failure path
        # via the explicit raise gives the operator the same
        # clear-flag-and-stop signal as a download error.
        raise DownloadError(f'yt-dlp returned no info for {uri!r}')

    # ``noplaylist=True`` collapses single-video extractions; for
    # the playlist edge cases yt-dlp falls through to, the first
    # entry is the one we downloaded.
    if info.get('_type') == 'playlist':
        entries = info.get('entries') or []
        info = entries[0] if entries else info

    title = info.get('title') or asset.name
    raw_duration = info.get('duration')
    duration: int | None
    if raw_duration is None:
        duration = None
    else:
        try:
            # Floor to 1 so a sub-second clip can't slot a 0s entry
            # into the viewer rotation.
            duration = max(1, int(float(raw_duration)))
        except (TypeError, ValueError):
            duration = None

    # Stamp the metadata bag the same way normalize_video_asset
    # would: ``source='youtube'`` so an operator (or future failure
    # diagnostic) can tell at a glance where the row came from, and
    # ``source_url`` so the original watch URL is recoverable even
    # after ``name`` is overwritten with the resolved title.
    metadata = dict(asset.metadata or {})
    metadata.update(
        {
            'source': 'youtube',
            'source_url': uri,
        }
    )
    metadata.pop('error_message', None)

    update: dict[str, Any] = {
        # ``is_processing`` deliberately stays True — normalize_video
        # below clears it once its probe + (optional) transcode
        # finishes. A single state transition (Processing → Done)
        # reads better in the table than the previous two-step
        # (Processing → Done → maybe-still-needs-work).
        'name': title,
        'metadata': metadata,
    }
    if duration is not None:
        update['duration'] = duration
    # Backfill the row's uri when we used the defensive fallback
    # above. Without this the file lands at ``location`` but
    # Asset.uri stays empty, so the viewer's filesystem check and
    # the API's serialised uri both come back missing. Only writes
    # when uri was falsy on entry — leaves an operator-set uri
    # alone in the normal path.
    if not asset.uri:
        update['uri'] = location
    Asset.objects.filter(asset_id=asset_id).update(**update)

    # Browser-side nudge so the table picks up the resolved title +
    # duration immediately. The "Processing" pill stays on (the row
    # is still ``is_processing=True``) until normalize_video_asset
    # finishes its pass and clears the flag — at which point the
    # same _notify helper fires again from the normalize task and
    # the pill drops. ``reload_viewer=False`` keeps this
    # intermediate hop browser-only: the on-device viewer doesn't
    # need to reload its playlist for a row that's still in the
    # processing state, and the chained normalize step's _notify
    # will publish the reload once the file is final. Saves the
    # viewer one redundant playlist refresh on every YouTube
    # upload — meaningful on a Pi 1/Zero where the reload
    # rebuilds the rotation against the SD card.
    from anthias_server.processing import (
        _notify,
        dispatch_normalize_video,
    )

    _notify(asset_id, reload_viewer=False)

    # Hand off to the per-board normalisation pass. This is what
    # gives YouTube downloads the same codec / container guarantees
    # as direct file uploads: ffprobe → passthrough on H.264/HEVC
    # (per board profile) or transcode to libx264/libx265 otherwise.
    # It also writes ``original_ext`` / ``transcoded`` /
    # ``transcode_target`` to metadata, so the operator's view of a
    # YouTube row carries the same diagnostic shape as a file upload.
    dispatch_normalize_video(asset_id)


@celery.task
def reboot_anthias() -> None:
    """
    Background task to reboot Anthias
    """
    if is_balena_app():
        for attempt in Retrying(
            stop=stop_after_attempt(5),
            wait=wait_fixed(1),
        ):
            with attempt:
                reboot_via_balena_supervisor()
    else:
        r.publish('hostcmd', 'reboot')


@celery.task
def shutdown_anthias() -> None:
    """
    Background task to shutdown Anthias
    """
    if is_balena_app():
        for attempt in Retrying(
            stop=stop_after_attempt(5),
            wait=wait_fixed(1),
        ):
            with attempt:
                shutdown_via_balena_supervisor()
    else:
        r.publish('hostcmd', 'shutdown')


def _check_asset_reachability(asset: Asset) -> bool:
    """Return True if the asset's URI is reachable.

    Local files: existence check. Remote URIs: defer to ``url_fails``,
    which knows about both HTTP(S) and streaming (RTSP/RTMP) probes.
    Trust ``skip_asset_check`` — operator opted out of validation.
    """
    if asset.skip_asset_check:
        return True
    uri = asset.uri or ''
    if uri.startswith('/'):
        return path.isfile(uri)
    return not url_fails(uri)


@celery.task(time_limit=ASSET_REVALIDATION_TIME_LIMIT_S)
def revalidate_asset_urls() -> None:
    """Refresh ``Asset.is_reachable`` for every enabled asset.

    Runs on the celery-beat schedule registered in
    ``setup_periodic_tasks``. Skips disabled and in-progress assets
    (an in-flight youtube_asset download can still be writing the file
    out, so a probe is meaningless until it lands). The probe itself
    is delegated to ``url_fails``, which already knows the rules for
    streaming vs HTTP and caps RTSP probes at 15s wall-clock.

    A Redis lock guards against overlap. A streaming-heavy playlist
    can have a worst-case sweep duration approaching the periodic
    interval (15s per RTSP probe, 20s per HTTP HEAD+GET timeout).
    Without the lock, the next beat tick would enqueue a second sweep
    while the first is still running, and we'd end up with multiple
    workers hammering the same asset list and racing on the
    ``Asset.objects.filter(...).update()`` writes.

    The lock value is a unique per-sweep token, and release is a Lua
    compare-and-delete. The token guards a pathological case: if our
    TTL expires while we're still running and a fresh sweep acquires
    the lock, our ``finally`` block must NOT delete that fresh lock.
    """
    from django.utils import timezone

    # SETNX with TTL and a unique token: succeeds only if the key
    # isn't held. The TTL matches the task time_limit so a hard kill
    # doesn't leave the lock orphaned. The token lets us release
    # safely even if the TTL expired and someone else now owns it.
    token = secrets.token_hex(16)
    if not r.set(
        ASSET_REVALIDATION_LOCK_KEY,
        token,
        nx=True,
        ex=ASSET_REVALIDATION_TIME_LIMIT_S,
    ):
        logging.info(
            'revalidate_asset_urls: previous sweep still running, skipping'
        )
        return

    try:
        qs = Asset.objects.filter(is_enabled=True, is_processing=False)
        for asset in qs:
            if asset.skip_asset_check:
                # No probe runs for these rows (the operator opted
                # out of validation), so don't update
                # ``last_reachability_check`` either — the API
                # exposes that field as "last check" and writing it
                # without an actual probe would advertise a check
                # that never happened. is_reachable is left at its
                # default (True), which matches what the viewer's
                # _asset_is_displayable expects for skip_asset_check
                # rows.
                continue
            try:
                reachable = _check_asset_reachability(asset)
            except Exception:
                # url_fails should swallow its own exceptions, but a
                # surprise from sh/requests shouldn't kill the whole sweep.
                logging.exception(
                    'revalidate_asset_urls: probe crashed for %s',
                    asset.asset_id,
                )
                continue
            Asset.objects.filter(asset_id=asset.asset_id).update(
                is_reachable=reachable,
                last_reachability_check=timezone.now(),
            )
    finally:
        # Compare-and-delete: only release if the lock still holds
        # *our* token. If the TTL expired and someone else acquired
        # the lock with a different token, leave it alone.
        r.eval(_LOCK_RELEASE_LUA, 1, ASSET_REVALIDATION_LOCK_KEY, token)


def _parse_processing_started_at(value: Any) -> datetime | None:
    """Best-effort ISO-8601 parser for ``metadata.processing_started_at``.

    Returns a tz-aware ``datetime`` on success, ``None`` on any failure.
    Anthias never writes a non-string value to this key, but a backup
    restored from a hand-edited JSON dump might. Treat unparseable
    values as "missing" — the reconciler then stamps the row on first
    sight, the same recovery path the legacy / pre-stamp branch takes.

    Naive datetimes (no ``tzinfo``) are also treated as missing — the
    dispatch helpers stamp via ``timezone.now()`` which is tz-aware
    under Django's ``USE_TZ=True``, so a naive value is by definition
    a hand-edit rather than something we wrote. Comparing a naive
    ``datetime`` to the tz-aware ``cutoff`` below would raise
    ``TypeError`` and abort the sweep — returning ``None`` here
    routes the row through the stamp-on-first-sight branch instead,
    which is the right behaviour for a malformed value (same as the
    parse-error branch).
    """
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


@celery.task(time_limit=300)
def reconcile_stuck_processing() -> None:
    """Recover ``Asset`` rows stuck at ``is_processing=True``.

    Causes (incomplete list):
      * v1/v1.1 file uploads pre-GH #2870 fix: the create view never
        dispatched ``normalize_video_asset`` / ``normalize_image_asset``,
        so the row landed at ``is_processing=True`` and stayed there.
      * Celery worker killed mid-task (SIGKILL, OOM, container
        restart) before ``on_failure`` could clear the flag.
      * Backup restore that re-created rows with ``is_processing=True``.
      * Redis flake during ``.delay()`` that ate the enqueue.

    Lookup: every ``is_processing=True`` row gets its
    ``metadata.processing_started_at`` examined. The dispatch helpers
    (``dispatch_normalize_image`` / ``dispatch_normalize_video`` /
    ``dispatch_download``) stamp this field at dispatch time. Rows
    without the stamp are legacy / backup-restored / pre-stamp; they
    get stamped on first sweep with ``timezone.now()``, so the
    *next* re-dispatch decision waits the full
    ``RECONCILE_STUCK_THRESHOLD_S`` (60 min) from that stamp — at
    a 10-min sweep cadence that's six sweeps where the row stays
    inside the grace window before becoming eligible. The grace is
    deliberately long enough to cover the worst-case live transcode
    (``NORMALIZE_VIDEO_TIME_LIMIT_S=30min``) plus margin, so a
    still-in-flight task never gets yanked out from under itself.

    Rows older than ``RECONCILE_STUCK_THRESHOLD_S`` (60 min) get
    re-dispatched via the normalisation path matching their mimetype.
    A re-dispatch refreshes ``processing_started_at`` automatically
    (the helper writes the field), so the next sweep won't ping-pong
    on the same row. A mimetype the reconciler doesn't know how to
    re-dispatch (e.g. a corrupted row with mimetype='webpage' marked
    as processing) lands on ``_set_processing_error`` — the flag
    clears and the operator sees an explicit "Failed" pill with a
    reconciler-sourced error message.

    Single-flighted via a Redis SETNX lock — mirrors
    ``revalidate_asset_urls``. The default Anthias deploy runs one
    celery worker with embedded beat, so this is a defence rather
    than a correctness requirement today; it bounds blast radius if a
    future deploy ever runs two workers with ``-B`` (or a separate
    ``celery beat`` instance) by ensuring only one sweep re-dispatches
    a given stuck row per tick.
    """
    from django.utils import timezone

    from anthias_server.processing import (
        _set_processing_error,
        dispatch_normalize_image,
        dispatch_normalize_video,
        stamp_processing_start,
    )

    # SETNX with TTL and a unique per-sweep token, same pattern as
    # revalidate_asset_urls. TTL matches the task time_limit so a
    # hard-killed worker doesn't orphan the lock; the Lua release
    # below is compare-and-delete so a stale-TTL → fresh-acquisition
    # → original-finally ordering can't clobber the new holder.
    token = secrets.token_hex(16)
    if not r.set(
        RECONCILE_STUCK_LOCK_KEY,
        token,
        nx=True,
        ex=300,  # matches the task's time_limit
    ):
        logging.info(
            'reconcile_stuck_processing: previous sweep still running, '
            'skipping'
        )
        return

    try:
        now = timezone.now()
        cutoff = now - timedelta(seconds=RECONCILE_STUCK_THRESHOLD_S)

        for asset in Asset.objects.filter(is_processing=True):
            metadata = asset.metadata or {}
            started_at = _parse_processing_started_at(
                metadata.get('processing_started_at')
            )

            if started_at is None:
                # Legacy / backup-restored / pre-stamp row. Mark its
                # start so the next sweep can apply the age threshold
                # uniformly — a still-in-flight task gets the full
                # grace window rather than being yanked out from
                # under itself.
                stamp_processing_start(asset.asset_id)
                continue

            if started_at > cutoff:
                # Inside the grace window. Let the (presumed-running)
                # task complete on its own.
                continue

            # Stuck past the threshold. Route by mimetype.
            mimetype = (asset.mimetype or '').lower()
            if mimetype == 'image':
                logging.warning(
                    'reconcile_stuck_processing: re-dispatching image '
                    'normalize for %s (stuck since %s)',
                    asset.asset_id,
                    started_at.isoformat(),
                )
                dispatch_normalize_image(asset.asset_id)
            elif mimetype == 'video':
                logging.warning(
                    'reconcile_stuck_processing: re-dispatching video '
                    'normalize for %s (stuck since %s)',
                    asset.asset_id,
                    started_at.isoformat(),
                )
                dispatch_normalize_video(asset.asset_id)
            else:
                # No clear task to re-dispatch — clear the flag with
                # a logged error so the operator can interact with
                # the row. YouTube rows arrive here as
                # mimetype='video' (the create path rewrites the row
                # to video at the same time it enqueues
                # download_youtube_asset), so the YouTube case is
                # covered by the video branch above — we lose the
                # original watch URL on a backup-restored YouTube
                # row, but video-pipeline re-dispatch is the best we
                # can do without re-querying yt-dlp.
                logging.warning(
                    'reconcile_stuck_processing: clearing flag on '
                    'unknown-mimetype row %s '
                    '(mimetype=%r, stuck since %s)',
                    asset.asset_id,
                    asset.mimetype,
                    started_at.isoformat(),
                )
                _set_processing_error(
                    asset.asset_id,
                    'Processing stalled past threshold; flag cleared '
                    'by reconciler. Re-upload the asset to retry.',
                )
    finally:
        # Compare-and-delete via the shared Lua script: only release
        # if the lock still holds *our* token. If our TTL expired and
        # someone else acquired the lock with a fresh token, leave it
        # alone.
        r.eval(_LOCK_RELEASE_LUA, 1, RECONCILE_STUCK_LOCK_KEY, token)


@celery.task(time_limit=30)
def revalidate_asset_url(asset_id: str) -> None:
    """On-demand probe for a single asset.

    Triggered when the viewer hits an asset it can't display, so the
    sweep doesn't have to be the only path that flips
    ``is_reachable``. Concurrency- and cooldown-gated by an atomic
    Redis SETNX on a per-asset key (TTL = RECHECK_COOLDOWN_S):
    multiple near-simultaneous tasks for the same asset_id no-op
    after the first one acquires the lock, and a viewer that loops
    through an unreachable asset every few seconds doesn't pin the
    worker on ffprobe.

    Mirrors the sweep's filtering on ``is_enabled`` /
    ``is_processing`` / ``skip_asset_check`` — probing a disabled or
    in-flight youtube_asset row would write misleading state, and
    skip_asset_check rows are explicitly opted out of validation.
    """
    from django.utils import timezone

    try:
        asset = Asset.objects.get(asset_id=asset_id)
    except Asset.DoesNotExist:
        return

    if not asset.is_enabled or asset.is_processing:
        # Mirror the sweep filter. Probing a disabled/in-flight row
        # would write state that's immediately moot.
        return

    if asset.skip_asset_check:
        # Operator opted out of validation; matches sweep behavior of
        # not touching is_reachable / last_reachability_check.
        return

    # Atomic cooldown gate. Replaces a previous timestamp-comparison
    # check that was racy under Celery worker concurrency: multiple
    # tasks for the same asset_id could all read the same stale
    # ``last_reachability_check`` and each decide they should probe.
    # Acquire it after eligibility checks so missing or temporarily
    # ineligible assets do not suppress a later legitimate recheck.
    if not r.set(
        asset_recheck_lock_key(asset_id),
        '1',
        nx=True,
        ex=RECHECK_COOLDOWN_S,
    ):
        return

    try:
        reachable = _check_asset_reachability(asset)
    except Exception:
        logging.exception(
            'revalidate_asset_url: probe crashed for %s', asset_id
        )
        return
    Asset.objects.filter(asset_id=asset_id).update(
        is_reachable=reachable,
        last_reachability_check=timezone.now(),
    )


# ---------------------------------------------------------------------------
# Asset normalisation pipeline (image / video)
# ---------------------------------------------------------------------------
#
# Implementation lives in ``anthias_server.processing`` to keep this
# file focused on the existing celery surface and to make the
# unit-test boundary explicit (the module functions are called
# directly without going through Celery). The thin wrappers below are
# the actual celery.task entry points so the task names register on
# the worker and ``apply_async`` works out of the box.
from anthias_server import processing  # noqa: E402

NORMALIZE_VIDEO_TIME_LIMIT_S = processing.NORMALIZE_VIDEO_TIME_LIMIT_S


@celery.task(
    base=processing._NormalizeAssetTask,
    time_limit=300,
    autoretry_for=(OSError,),
    # Two OSError subclasses are permanent and must NOT trigger a
    # retry — they'd just keep the row in ``is_processing=True``
    # longer before the inevitable on_failure lands:
    #   * ``FileNotFoundError`` — source file gone between row
    #     creation and pickup (cleanup raced operator, disk pressure).
    #   * ``UnidentifiedImageError`` — Pillow decoded the header far
    #     enough to refuse the file (corrupt HEIC, truncated TIFF,
    #     mis-typed extension). It inherits from OSError so the
    #     autoretry_for filter would otherwise sweep it up; listing
    #     it explicitly here makes the failure surface immediately.
    dont_autoretry_for=(FileNotFoundError, UnidentifiedImageError),
    retry_backoff=10,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=2,
)
def normalize_image_asset(asset_id: str) -> None:
    """Convert every extension in
    ``processing.NORMALIZE_IMAGE_EXTS`` (HEIC / HEIF / TIFF / BMP /
    ICO / TGA / JPEG 2000 family / AVIF) to lossless WebP.

    No-ops if the row is missing or has already been finalised
    (duplicate task fire / operator-edited row). Permanent decode
    failures (corrupt HEIC) raise so ``_NormalizeAssetTask.on_failure``
    writes ``metadata.error_message`` and clears ``is_processing``.

    Retries on OSError cover transient disk pressure / a temporary
    libheif read hiccup. Two OSError subclasses are excluded from
    autoretry via ``dont_autoretry_for`` because they're permanent:
    ``FileNotFoundError`` (source file gone) and Pillow's
    ``UnidentifiedImageError`` (corrupt input). See the decorator's
    inline comment for the rationale.
    """
    asset = processing._row_or_none(asset_id)
    if asset is None:
        return
    processing._run_image_normalisation(asset)


@celery.task(
    base=processing._NormalizeAssetTask,
    time_limit=NORMALIZE_VIDEO_TIME_LIMIT_S,
    autoretry_for=(OSError,),
    # Same rationale as normalize_image_asset above: a missing source
    # file is permanent and should land on on_failure right away.
    dont_autoretry_for=(FileNotFoundError,),
    retry_backoff=15,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=1,
)
def normalize_video_asset(asset_id: str) -> None:
    """Probe the upload; passthrough or transcode to a board-appropriate
    codec in MP4.

    The output codec is decided by ``processing.compute_envelope``:
    libx264 on legacy Pi 2/Pi 3 (mmal-vc4 path; no HEVC hardware) and
    libx265 with the iOS-friendly ``-tag:v hvc1`` on Pi 4-64 / Pi 5 /
    x86 (mpv path; HEVC hardware-decoded on Pi 4 / x86, software on
    Pi 5). The on-device player only ever sees a codec it can decode.

    ffmpeg is wrapped with ``-threads 2`` so two cores stay free for
    the on-device viewer; the celery worker itself runs under
    ``nice -n 19 ionice -c 3`` (set in docker-compose.yml.tmpl).

    Retry policy mirrors ``download_youtube_asset``: OSError gets one
    retry (transient IO), ffmpeg subprocess failures and timeouts are
    permanent and land on on_failure.
    """
    asset = processing._row_or_none(asset_id)
    if asset is None:
        return
    processing._run_video_normalisation(asset)


@celery.task(time_limit=300)
def regenerate_for_envelope_change(force: bool = False) -> int:
    """Walk every video ``Asset`` and queue a re-render for any
    whose ``metadata['envelope']`` no longer matches the current
    ``compute_envelope()``.

    Called from the anthias-server startup hook (see
    ``anthias_server.apps``) on every boot. Cheap when nothing has
    changed — one ``compute_envelope()`` + one
    ``Asset.objects.filter(mimetype='video')`` walk, comparing each
    row's recorded envelope dict against the current one.

    For each stale row:

    1. ``Asset.objects.filter(asset_id=...).update(is_processing=True)``
       so the viewer skips the row during re-render (matches the
       upload-time contract).
    2. ``stamp_processing_start(asset_id)`` lays the timestamp the
       periodic ``reconcile_stuck_processing`` task uses to age-gate
       retries, so a stuck row gets the same recovery the upload
       path enjoys.
    3. ``normalize_video_asset.delay(asset_id)`` drops the work on
       the same celery queue the upload-time normalize uses. The
       refactored task reads the sibling ``.original.<ext>`` to
       re-render (or treats the existing variant as the original on
       first pass, for legacy assets pre-envelope rollout).

    Returns the number of rows queued so the caller / log surface
    has a "0 → all caught up" / "N → walker found work" signal.

    ``force=True`` re-queues every video asset regardless of
    envelope match — used by the ``reset-envelope`` manage.py
    command and the failure-mode test that injects a corrupt
    cache.

    Failures are non-fatal: any per-row exception is logged but
    doesn't stop the walker. We want a single bad row to not block
    the rest of the catalog from getting their fresh variants.
    """
    from anthias_server.app.models import Asset
    from anthias_server.playback_envelope import (
        PlaybackEnvelope,
        compute_envelope,
    )

    current = compute_envelope()
    current_dict = current.as_dict()
    queued = 0
    for asset in Asset.objects.filter(mimetype='video'):
        metadata = asset.metadata or {}
        recorded = metadata.get('envelope')
        if not force and recorded == current_dict:
            # Variant on disk matches current envelope — nothing to
            # do. Equality on the JSON-dict form is the right test:
            # both sides came through ``PlaybackEnvelope.as_dict``,
            # which has stable key order.
            continue
        # Validate any non-None recorded envelope so a corrupt
        # entry triggers re-render rather than silently passing the
        # `recorded == current_dict` check on bytewise-different
        # JSON. We don't *use* the parsed value beyond catching
        # malformed entries.
        if recorded is not None:
            try:
                PlaybackEnvelope.from_dict(recorded)
            except (ValueError, TypeError, KeyError):
                logging.info(
                    'regenerate_for_envelope_change: asset %s has '
                    "malformed metadata['envelope']; treating as stale",
                    asset.asset_id,
                )
        try:
            Asset.objects.filter(asset_id=asset.asset_id).update(
                is_processing=True,
            )
            processing.stamp_processing_start(asset.asset_id)
            normalize_video_asset.delay(asset.asset_id)
            queued += 1
        except Exception:
            logging.exception(
                'regenerate_for_envelope_change: queueing asset %s failed; '
                'continuing with the rest of the catalog',
                asset.asset_id,
            )
    if queued:
        logging.info(
            'regenerate_for_envelope_change: queued %d video asset(s) '
            'for re-render against envelope=%s',
            queued,
            current_dict,
        )
    return queued
