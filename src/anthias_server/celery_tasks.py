import logging
import os
import secrets
import time
from datetime import timedelta
from os import getenv, path
from typing import Any

import django
import sh
from celery import Celery, Task
from tenacity import Retrying, stop_after_attempt, wait_fixed

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
    asset_dir_real = path.realpath(asset_dir)
    referenced = set()
    for uri in (
        Asset.objects.exclude(uri__isnull=True)
        .exclude(uri__exact='')
        .values_list('uri', flat=True)
    ):
        if not uri:
            continue
        try:
            if path.realpath(path.dirname(uri)) == asset_dir_real:
                referenced.add(path.basename(uri))
        except OSError:
            continue
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
    """Custom Task subclass so ``on_failure`` clears ``is_processing``
    when the download fails, mirroring ``_ProbeVideoTask``.

    The previous in-process daemon thread swallowed yt-dlp's exit
    code, so a failed download silently left the row stuck at
    "Processing" with an empty .mp4 — the operator had no way to
    unblock it without editing the database. Now any uncaught
    exception (DownloadError, ExtractorError, ...) bubbles to celery
    and lands here once retries are exhausted.
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
            from anthias_server.app.consumers import notify_asset_update

            notify_asset_update(asset_id)
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
    ``name`` (with the resolved title) and ``duration``, then clears
    ``is_processing`` and nudges the viewer + browser the same way
    ``probe_video_duration`` does.

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

    update: dict[str, Any] = {
        'is_processing': False,
        'name': title,
    }
    if duration is not None:
        update['duration'] = duration
    Asset.objects.filter(asset_id=asset_id).update(**update)

    # Tell the viewer to reload its playlist now that the file
    # exists and the row is fully materialised.
    r.publish('anthias.viewer', 'reload')

    # Browser-side nudge so the table drops "Processing" and picks
    # up the resolved title/duration without waiting for the 5s poll.
    from anthias_server.app.consumers import notify_asset_update

    notify_asset_update(asset_id)


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
