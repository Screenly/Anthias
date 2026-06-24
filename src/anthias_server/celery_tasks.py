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
from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import worker_init
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
# the longest reasonable Celery task: ``YOUTUBE_DOWNLOAD_TIME_LIMIT_S``
# is 15 min, so 60 min is a safe floor. A row past the threshold
# either had its worker time-limit expire (in which case
# ``on_failure`` should already have
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

# Time budget for a single on-demand probe. A legitimate worst case
# under url_fails comfortably exceeds the previous 30s hard limit: a
# hanging getaddrinfo against a broken resolver (no timeout knob
# exists for it), then an HTTP HEAD (10s) plus the GET fallback
# (10s). Tripping the *hard* limit SIGKILLs the pool child, which
# Sentry reported as three separate issues per occurrence — the
# "Hard time limit exceeded" error, the TimeLimitExceeded raise, and
# the "ForkPoolWorker exited with signal 9" billiard log (ANTHIAS-A /
# ANTHIAS-9 / ANTHIAS-B). The soft limit raises
# ``SoftTimeLimitExceeded`` *inside* the task instead, which the task
# records as an ordinary failed probe; the hard limit stays as the
# backstop for a probe stuck in C code where the soft signal can't be
# delivered.
ASSET_RECHECK_SOFT_TIME_LIMIT_S = 60
ASSET_RECHECK_TIME_LIMIT_S = 90

# Hard ceiling on how long a single sweep is allowed to run. Set above
# the periodic interval so a sweep that's running long doesn't get
# guillotined while the next beat tick races to start a new one — the
# Redis lock below is the primary overlap guard, the time_limit just
# bounds pathological cases (broken DNS resolver, a stuck ffprobe).
ASSET_REVALIDATION_TIME_LIMIT_S = 60 * 30

# Soft companion to the sweep's hard limit: raise
# ``SoftTimeLimitExceeded`` inside the loop a minute early so the
# sweep can abort cleanly (release its lock, keep the rows updated so
# far) instead of being SIGKILLed by the hard limit — the same
# kill-versus-catch rationale as the on-demand probe limits above.
ASSET_REVALIDATION_SOFT_TIME_LIMIT_S = ASSET_REVALIDATION_TIME_LIMIT_S - 60

# Time budget for the lightweight periodic pokes — the display-power
# CEC query and the telemetry POST. Both ran under a bare
# ``time_limit=30`` and share the asset probe's failure mode: the CEC
# query can wedge inside libcec, and the telemetry POST can stall in
# getaddrinfo against a broken resolver (requests' ``timeout`` covers
# connect/read but not DNS). Tripping the *hard* limit SIGKILLs the
# pool child, which Sentry groups by the kill signature regardless of
# task — so these two kept the ANTHIAS-A / ANTHIAS-9 / ANTHIAS-B trio
# alive after #3017 fixed only the asset probe. The soft limit raises
# ``SoftTimeLimitExceeded`` *inside* the task so it logs and skips the
# tick cleanly; the hard limit stays as the backstop for a call stuck
# in C code where the soft signal can't be delivered.
PERIODIC_POKE_SOFT_TIME_LIMIT_S = 30
PERIODIC_POKE_TIME_LIMIT_S = 60

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


# Poll cadence while the worker waits for the server's startup
# ``migrate`` pass to finish, and how often a waiting worker repeats
# its log line (see ``wait_for_migrations`` below).
MIGRATION_WAIT_POLL_S = 5
MIGRATION_WAIT_LOG_EVERY_S = 30


def _migrations_ready() -> bool:
    """True when the shared SQLite schema is fully migrated.

    Computes the same unapplied-migration plan ``manage.py migrate
    --check`` does. A database-side error getting there
    (``OperationalError: database is locked`` while the server's
    dbbackup/dbrestore holds the file, a missing
    ``django_migrations`` table on a first-boot/empty DB) means the
    schema is not ready either, so report not-ready rather than
    raising; the underlying error is kept visible at DEBUG for runs
    with ``--loglevel=debug``. Anything that is NOT a database error
    (a programming bug in the probe itself) propagates so the worker
    fails fast instead of waiting forever.
    """
    from django.db import connections
    from django.db.utils import DatabaseError
    from django.db.migrations.executor import MigrationExecutor

    connection = connections['default']
    try:
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    except DatabaseError:
        logging.debug(
            'Migration-readiness probe failed; treating as not ready',
            exc_info=True,
        )
        return False
    finally:
        # Don't leak the probe connection into the prefork children —
        # SQLite handles must not be shared across fork().
        connection.close()
    return not plan


@worker_init.connect
def wait_for_migrations(**kwargs: Any) -> None:
    """Block worker startup until the server has applied migrations.

    The celery container starts in parallel with anthias-server, whose
    start script is still running its dbbackup → migrate pass (or the
    dbrestore fallback, which drops and re-creates every table). A
    task replayed off the Redis broker in that window dies with
    ``OperationalError: no such table: assets`` (Sentry ANTHIAS-1 —
    one burst per device on every upgrade/first boot). Tasks stay
    queued in the broker while we wait, so nothing is lost — they run
    as soon as the schema is in place. Deliberately unbounded: a
    worker without a database can do no useful work, and the log line
    below keeps the wait observable.
    """
    waited = 0
    next_log_at = 0
    while not _migrations_ready():
        # First miss logs immediately, then repeat every
        # MIGRATION_WAIT_LOG_EVERY_S — a multi-minute migrate/restore
        # on a slow SD card shouldn't drown the device log in a
        # warning per poll. Tracked as an explicit next-log threshold
        # so the cadence holds whatever the two constants are set to.
        if waited >= next_log_at:
            logging.warning(
                'Database is not migrated yet; delaying celery worker '
                'startup (%ss elapsed; probe errors are logged at '
                'DEBUG)',
                waited,
            )
            next_log_at = waited + MIGRATION_WAIT_LOG_EVERY_S
        time.sleep(MIGRATION_WAIT_POLL_S)
        waited += MIGRATION_WAIT_POLL_S


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


@celery.task(
    soft_time_limit=PERIODIC_POKE_SOFT_TIME_LIMIT_S,
    time_limit=PERIODIC_POKE_TIME_LIMIT_S,
)
def get_display_power() -> None:
    # diagnostics.get_display_power() returns ``str | bool`` (bool for
    # a clean CEC True/False, str for the error fallbacks). redis-py
    # refuses a bool — ``DataError: Invalid input of type: 'bool'`` —
    # so every successful power query crashed this task and left the
    # key unset (Sentry ANTHIAS-2C). Coerce to str: the v2 System Info
    # API exposes ``display_power`` as ``string | null`` and just
    # passes the value through, so 'True'/'False'/'CEC error' all fit
    # — and the on/off state now actually populates instead of only
    # the error cases ever landing.
    try:
        # Single SET with ex= so the value and its TTL are written
        # atomically — a soft-limit signal landing between a separate
        # SET and EXPIRE would otherwise leave the key without a TTL
        # (a stale display_power that never expires).
        r.set('display_power', str(diagnostics.get_display_power()), ex=3600)
    except SoftTimeLimitExceeded:
        # The CEC query is meant to be bounded by its own
        # subprocess timeout, but a child wedged in libcec can keep
        # the pipe open past it. Skip this tick rather than let the
        # hard limit SIGKILL the worker (ANTHIAS-A / 9 / B); the next
        # beat tick re-queries.
        logging.warning(
            'get_display_power: CEC query exceeded %ss; skipping this tick',
            PERIODIC_POKE_SOFT_TIME_LIMIT_S,
        )


@celery.task(
    soft_time_limit=PERIODIC_POKE_SOFT_TIME_LIMIT_S,
    time_limit=PERIODIC_POKE_TIME_LIMIT_S,
)
def send_telemetry_task() -> None:
    try:
        send_telemetry()
    except SoftTimeLimitExceeded:
        # requests' timeout doesn't cover a getaddrinfo stall against
        # a broken resolver, so the POST can outlive the soft budget.
        # Skip this tick instead of being SIGKILLed by the hard limit
        # (ANTHIAS-A / 9 / B); send_telemetry didn't set its cooldown,
        # so the next beat tick retries.
        logging.warning(
            'send_telemetry_task: telemetry POST exceeded %ss; '
            'skipping this tick',
            PERIODIC_POKE_SOFT_TIME_LIMIT_S,
        )


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

    for uri in Asset.objects.exclude(uri__isnull=True).values_list(
        'uri', flat=True
    ):
        _claim(uri)
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


class _DownloadAssetTask(Task):  # type: ignore[type-arg]
    """Shared ``on_failure`` for the download tasks (YouTube +
    generic remote video).

    Both surface a permanent failure to the operator the same way a
    failed HEIC conversion does: ``is_processing=False`` and a
    populated ``metadata.error_message`` that the asset table renders
    as a "Failed" pill with the message on the hover tooltip. Without
    that unification, a yt-dlp DownloadError (or a ``RemoteVideoDownload
    Error``) would clear the Processing pill but leave no on-row
    diagnostic — the operator couldn't tell a fresh download from a
    404'd one.

    Subclasses override ``_failure_log_prefix`` so the worker log line
    names the actual task that failed.
    """

    _failure_log_prefix: str = 'download_asset'

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
            # Reuse the helpers in anthias_server.processing so every
            # download / normalize task shares the exact same
            # "row failed" semantics — single source of truth for the
            # error_message contract instead of near-duplicate blocks
            # that could drift.
            from anthias_server.processing import (
                _notify,
                _set_processing_error,
            )

            _set_processing_error(asset_id, f'{type(exc).__name__}: {exc}')
            _notify(asset_id)
        except Exception:
            logging.exception(
                '%s on_failure cleanup failed for %s',
                self._failure_log_prefix,
                asset_id,
            )


class _DownloadYoutubeTask(_DownloadAssetTask):
    """YouTube-specific download task. See ``_DownloadAssetTask`` for
    the failure contract.

    The previous in-process daemon thread swallowed yt-dlp's exit
    code, so a failed download silently left the row stuck at
    "Processing" with an empty .mp4. Now any uncaught exception
    (DownloadError, ExtractorError, ...) bubbles to celery and lands
    on the inherited ``on_failure`` once retries are exhausted.
    """

    _failure_log_prefix = 'download_youtube_asset'


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

    # Bias yt-dlp toward the board's hardware-decoded codec so playback
    # is as efficient as possible. Pi 5 prefers HEVC (VideoCore VII HW
    # path) but also accepts H.264 via software decode on Cortex-A76 —
    # YouTube rarely serves HEVC so H.264 is the realistic outcome.
    # Boards without HEVC (Pi 2/3) fall back to H.264. Unknown boards
    # default to H.264; normalize_video_asset gates on the actual codec.
    from anthias_server.processing import _hw_decoded_codecs
    _supported = _hw_decoded_codecs()
    # Prefer HEVC when the board can hardware-decode it; fall back to H.264.
    _preferred_vcodec = 'hevc' if 'hevc' in _supported else 'h264'

    ydl_opts = {
        # ``format_sort`` biases toward the board's preferred codec,
        # m4a audio, 1080p resolution, and higher fps. yt-dlp still
        # picks the *best matching* format, falling back to whatever
        # is available if no exact match exists. Strict ``format=``
        # filters would reject videos that only have other codecs,
        # which we don't want — normalize_video_asset handles that.
        'format_sort': [f'vcodec:{_preferred_vcodec}', 'fps', 'res:1080', 'acodec:m4a'],
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

    # Hand off to ``normalize_video_asset`` so the YouTube download
    # gets the same ffprobe metadata pass (codec / dims / fps /
    # duration written into ``metadata``) as a direct file upload.
    dispatch_normalize_video(asset_id)


# ---------------------------------------------------------------------------
# download_remote_video_asset — generic http(s) single-file video URLs
# ---------------------------------------------------------------------------
#
# Mirrors the YouTube lifecycle for any ``http(s)://…`` URL whose
# extension or Content-Type identifies it as a downloadable video
# container (mp4 / webm / mov / mkv / ...). The serializer flips the
# row to ``is_processing=True`` and points ``Asset.uri`` at a local
# destination path; this task fetches the file, stamps metadata, and
# chains into ``normalize_video_asset`` for the per-board HW-codec
# gate — the same path YouTube downloads already follow. The win is
# uniformity: a 4K H.264 URL on a Pi 5 no longer silently SW-decodes
# at rotation time, origin downtime no longer turns into mid-rotation
# black-screen slots, and operators see codec/dims/fps in the asset
# table.
#
# Live streams (HLS / DASH / RTSP) are excluded by the serializer's
# classify step — they reach this task only via mis-routing, which
# the explicit Content-Type guard below catches and rejects.


# Wall-clock cap on a single download attempt. Matches
# YOUTUBE_DOWNLOAD_TIME_LIMIT_S so an operator who pastes a 1080p
# clip URL gets the same patience as a YouTube link.
REMOTE_VIDEO_DOWNLOAD_TIME_LIMIT_S = 60 * 15

# Hard ceiling on the downloaded file size. 5 GiB covers >99% of
# legitimate signage content (a 2-hour 4K H.264 at 5 Mbps is ~4.5 GB;
# typical 5-minute 1080p clips are ~150 MB). The cap is enforced
# *during* the stream — a malicious or misconfigured origin can't
# blow out the device's SD card by advertising a small file and then
# serving terabytes. Exceeding the cap raises a hard error so the
# operator sees the row land as Failed with a clear message, rather
# than silently truncating to the cap.
REMOTE_VIDEO_MAX_BYTES = 5 * 1024**3

# Connect / read timeouts. The connect timeout is short because a
# legitimate origin establishes the TCP + TLS handshake in well under
# a second; a 15s ceiling tolerates a slow DNS resolver or a sleepy
# CDN edge. The read timeout is per-chunk — a stalled stream that
# doesn't send any bytes for 60s gets killed rather than tying up
# the worker for the full 15-minute time_limit.
REMOTE_VIDEO_CONNECT_TIMEOUT_S = 15
REMOTE_VIDEO_READ_TIMEOUT_S = 60

# Chunk size for the streaming download. 1 MiB is the sweet spot for
# the Pi-class SD-card writer: smaller chunks add per-write syscall
# overhead, larger chunks tie up RAM that could be feeding the
# kernel page cache. Same value used elsewhere in the codebase for
# bulk file IO.
_REMOTE_VIDEO_CHUNK_BYTES = 1024 * 1024

# Manifest Content-Types we explicitly reject at GET time even though
# the upfront HEAD probe in the serializer should have caught them.
# Some origins serve different headers on HEAD vs GET (HEAD returns
# 200 with ``video/mp4``, GET returns 200 with
# ``application/vnd.apple.mpegurl``) — defence in depth rather than
# trust the serializer's upstream classify.
_REMOTE_VIDEO_MANIFEST_CONTENT_TYPES = frozenset(
    {
        'application/vnd.apple.mpegurl',
        'application/x-mpegurl',
        'application/dash+xml',
    }
)


# Module-level session — same UA convention as the HEAD probe in
# ``anthias_common.remote_video``. Tests patch ``_session.get``.
# Lazy import so the symbol resolves after Django's apps_ready.
from anthias_common.http import AnthiasSession  # noqa: E402

_session = AnthiasSession()


class RemoteVideoDownloadError(Exception):
    """Raised by ``download_remote_video_asset`` for permanent
    failures the operator needs to see on the row.

    Covers: non-2xx HTTP response, wrong Content-Type, file exceeded
    the size cap, zero-byte response. All four are conditions where
    retrying would just keep failing — the row lands on
    ``on_failure`` and the operator sees the message on the Failed
    pill's hover tooltip.
    """


def _validate_remote_video_response(resp: Any, uri: str) -> None:
    """Reject non-2xx responses, manifest Content-Types, and anything
    that isn't ``video/*`` / ``application/octet-stream`` / empty.

    Extracted from ``download_remote_video_asset`` so the task body
    stays under SonarCloud's cognitive-complexity ceiling. The serializer
    pre-classifies via HEAD, but some origins return different headers
    on HEAD vs GET — these checks are the second line of defence.
    """
    if resp.status_code >= 400:
        raise RemoteVideoDownloadError(
            f'HTTP {resp.status_code} fetching {uri!r}'
        )
    content_type = (resp.headers.get('Content-Type') or '').lower()
    base_type = content_type.split(';', 1)[0].strip()
    if base_type in _REMOTE_VIDEO_MANIFEST_CONTENT_TYPES:
        raise RemoteVideoDownloadError(
            f'origin served streaming manifest '
            f'({base_type!r}) instead of a downloadable file; '
            'live streams are not auto-downloaded'
        )
    # Accept ``video/*`` and ``application/octet-stream`` (some CDNs
    # serve video files this way). Reject everything else, including
    # an empty Content-Type. Well-behaved origins always send one; a
    # missing header is a stronger signal of a misbehaving origin
    # than evidence of a real video — and accepting it would let an
    # HTML error page land on disk as a multi-GB asset, where the
    # row stays orphaned because the cleanup() sweep won't touch a
    # file that's still referenced by an (errored) row.
    if (
        base_type.startswith('video/')
        or base_type == 'application/octet-stream'
    ):
        return
    raise RemoteVideoDownloadError(
        f'unexpected Content-Type {base_type!r} from {uri!r}; '
        'expected video/* or application/octet-stream'
    )


def _stream_remote_video_to_file(uri: str, staging: str) -> None:
    """Fetch *uri* with the module-level session and stream it to
    *staging*, enforcing the size cap and validating the response
    headers. Raises ``RemoteVideoDownloadError`` on permanent
    failures and lets transient ``OSError`` /
    ``requests.RequestException`` (a subclass of ``OSError``)
    propagate for the caller's ``autoretry_for``.

    Caller is responsible for cleaning up the partial staging file
    on any exception path.
    """
    with _session.get(
        uri,
        stream=True,
        allow_redirects=True,
        timeout=(
            REMOTE_VIDEO_CONNECT_TIMEOUT_S,
            REMOTE_VIDEO_READ_TIMEOUT_S,
        ),
    ) as resp:
        _validate_remote_video_response(resp, uri)
        written = 0
        with open(staging, 'wb') as fh:
            for chunk in resp.iter_content(
                chunk_size=_REMOTE_VIDEO_CHUNK_BYTES
            ):
                if not chunk:
                    # iter_content yields empty bytes for keep-alive
                    # padding on some servers; skip rather than
                    # treating as EOF.
                    continue
                written += len(chunk)
                if written > REMOTE_VIDEO_MAX_BYTES:
                    raise RemoteVideoDownloadError(
                        f'download exceeded size cap of '
                        f'{REMOTE_VIDEO_MAX_BYTES} bytes for {uri!r}'
                    )
                fh.write(chunk)
        if written == 0:
            raise RemoteVideoDownloadError(
                f'origin returned zero bytes for {uri!r}'
            )


class _DownloadRemoteVideoTask(_DownloadAssetTask):
    """Generic remote-video download task. Inherits the failure
    contract from ``_DownloadAssetTask`` — only the log prefix
    differs.
    """

    _failure_log_prefix = 'download_remote_video_asset'


@celery.task(
    base=_DownloadRemoteVideoTask,
    time_limit=REMOTE_VIDEO_DOWNLOAD_TIME_LIMIT_S,
    # Transient network / IO hiccups retry; permanent classes
    # (RemoteVideoDownloadError covers HTTP 4xx, content-type
    # mismatch, size cap) bubble straight to on_failure.
    autoretry_for=(OSError,),
    retry_backoff=15,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=2,
)
def download_remote_video_asset(asset_id: str, uri: str) -> None:
    """Fetch *uri* into the row's persisted ``Asset.uri`` and chain
    into ``normalize_video_asset``.

    The row is created upstream with ``mimetype='video'``,
    ``is_processing=True``, and ``uri`` pointing at
    ``<assetdir>/<asset_id>.<ext>`` (the eventual local path). On
    success this task lands the file at that path, stamps
    ``metadata['source']='remote_url'`` so the origin URL is
    recoverable after a row edit, then dispatches
    ``normalize_video_asset`` for the per-board HW-codec gate. The
    row stays ``is_processing=True`` across the chain so the table
    shows a single Processing → Done transition.

    Failure surface:
      * non-2xx response → ``RemoteVideoDownloadError`` → on_failure
        writes ``metadata.error_message`` and clears the flag.
      * wrong Content-Type on GET (manifest, HTML error page) →
        same.
      * downloaded bytes > ``REMOTE_VIDEO_MAX_BYTES`` → same.
      * transient network / IO hiccup → ``autoretry_for`` retries
        twice with backoff; persistent failure lands on on_failure.

    Cleans up ``.part`` on every failure path so a partially-written
    download doesn't linger as orphan content for the cleanup sweep
    to deal with an hour later.
    """
    try:
        asset = Asset.objects.get(asset_id=asset_id)
    except Asset.DoesNotExist:
        return

    if not asset.is_processing:
        # Already finalised by a previous invocation, or operator-
        # edited. Don't re-download or clobber state.
        return

    # The serializer stamps ``Asset.uri`` at the eventual local
    # destination path before dispatching this task (the extension
    # is picked from the URL or the HEAD probe's Content-Type).
    # Trust that value rather than recomputing — recomputing the
    # extension here from the URL alone would diverge from what
    # the serializer chose for a HEAD-probed extensionless URL
    # (``video/webm`` → ``.webm`` at the serializer vs. ``.mp4``
    # default here). A row reaching this task with an empty uri is
    # a programming error (broken dispatch site, hand-edited row),
    # not something to paper over with a guess.
    if not asset.uri:
        raise RemoteVideoDownloadError(
            f'asset {asset_id!r} has no destination uri — refusing '
            'to download without a serializer-stamped path'
        )
    location = asset.uri
    staging = f'{location}.part'

    # Stream the response, then atomically swap into place. Both
    # phases share the cleanup contract: a partial ``.part`` left
    # behind would otherwise wait for the hourly ``cleanup()`` sweep
    # to clear — meanwhile an operator's next upload could trip a
    # "disk full" if the partial was multi-GB. ``OSError`` covers
    # both filesystem failures and ``requests.RequestException``
    # (which is an ``IOError``/``OSError`` subclass), so the single
    # ``except OSError`` re-raise is sufficient for the
    # ``autoretry_for`` to pick up.
    try:
        _stream_remote_video_to_file(uri, staging)
        os.replace(staging, location)
    except (OSError, RemoteVideoDownloadError):
        try:
            os.remove(staging)
        except OSError:
            pass
        raise

    metadata = dict(asset.metadata or {})
    metadata.update(
        {
            'source': 'remote_url',
            'source_url': uri,
        }
    )
    metadata.pop('error_message', None)

    update: dict[str, Any] = {
        # ``is_processing`` deliberately stays True — the chained
        # normalize_video_asset below clears it once its probe
        # finishes. Single Processing → Done transition reads
        # better than the previous two-step.
        'metadata': metadata,
    }
    # Same uri-backfill rule as download_youtube_asset: write the
    # row's uri only if it was empty on entry, otherwise leave the
    # operator-set value alone.
    if not asset.uri:
        update['uri'] = location
    Asset.objects.filter(asset_id=asset_id).update(**update)

    from anthias_server.processing import (
        _notify,
        dispatch_normalize_video,
    )

    # Dashboard nudge so the operator's table picks up the row's
    # progress without waiting for the 5s poll. Viewer reload is
    # deferred to the normalize chain (same as YouTube) — the row is
    # still ``is_processing=True`` and the on-device viewer would
    # just reload to a row it can't display.
    _notify(asset_id, reload_viewer=False)

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


@celery.task(
    time_limit=ASSET_REVALIDATION_TIME_LIMIT_S,
    soft_time_limit=ASSET_REVALIDATION_SOFT_TIME_LIMIT_S,
)
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
            except SoftTimeLimitExceeded:
                # Out of budget for the whole sweep — handled by the
                # outer except below. Re-raise past the blanket
                # Exception arm that would otherwise swallow it and
                # keep the sweep running into the hard limit.
                raise
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
    except SoftTimeLimitExceeded:
        # The soft signal is delivered asynchronously, so it can fire
        # anywhere in the loop — during a probe or during the DB
        # update — which is why the whole sweep body is covered, not
        # just _check_asset_reachability. Abort cleanly (the
        # ``finally`` below releases the lock; rows updated so far
        # keep their fresh state) instead of letting the hard limit
        # SIGKILL the pool child. The next beat tick starts over.
        logging.warning(
            'revalidate_asset_urls: sweep exceeded %ss; '
            'aborting until the next beat tick',
            ASSET_REVALIDATION_SOFT_TIME_LIMIT_S,
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
    deliberately longer than any single task's ``time_limit`` (the
    longest is the 15-min YouTube download) so a still-in-flight
    task never gets yanked out from under itself.

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


@celery.task(
    time_limit=ASSET_RECHECK_TIME_LIMIT_S,
    soft_time_limit=ASSET_RECHECK_SOFT_TIME_LIMIT_S,
)
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

    # The soft-limit signal is delivered asynchronously, so the outer
    # try covers the DB update as well as the probe — a delivery
    # landing between the probe returning and the UPDATE committing
    # must not escape as a task failure (that's the SIGKILL-adjacent
    # noise this task's limits exist to avoid).
    try:
        try:
            reachable = _check_asset_reachability(asset)
        except SoftTimeLimitExceeded:
            # A probe that can't finish inside the soft budget gets
            # the same verdict url_fails gives an HTTP timeout:
            # unreachable. Recording the verdict (rather than
            # bailing) keeps the viewer's _asset_is_displayable in
            # sync with reality and the cooldown lock prevents an
            # immediate re-probe storm.
            logging.warning(
                'revalidate_asset_url: probe for %s exceeded %ss; '
                'marking unreachable',
                asset_id,
                ASSET_RECHECK_SOFT_TIME_LIMIT_S,
            )
            reachable = False
        except Exception:
            logging.exception(
                'revalidate_asset_url: probe crashed for %s', asset_id
            )
            return
        Asset.objects.filter(asset_id=asset_id).update(
            is_reachable=reachable,
            last_reachability_check=timezone.now(),
        )
    except SoftTimeLimitExceeded:
        logging.warning(
            'revalidate_asset_url: soft time limit hit while '
            'finalising the probe for %s; giving up this recheck',
            asset_id,
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
    time_limit=120,
    autoretry_for=(OSError,),
    # Same rationale as normalize_image_asset above: a missing source
    # file is permanent and should land on on_failure right away.
    dont_autoretry_for=(FileNotFoundError,),
    # ``UnsupportedVideoCodecError`` is the codec/resolution gate's
    # deliberate, operator-facing rejection (Failed pill + re-encode
    # recipe via _NormalizeAssetTask.on_failure) — an expected outcome,
    # not a fault. Listing it in ``throws`` keeps Celery from logging it
    # at ERROR with a traceback, and sentry-sdk's CeleryIntegration
    # skips ``task.throws`` exceptions, so the gate stops flooding
    # Sentry (ANTHIAS-1J, ANTHIAS-20). on_failure still runs.
    throws=(processing.UnsupportedVideoCodecError,),
    retry_backoff=15,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=1,
)
def normalize_video_asset(asset_id: str) -> None:
    """Probe the upload with ffprobe, write codec / dims / fps /
    duration into ``metadata``, and clear ``is_processing``. The
    asset file is never rewritten — see
    ``processing._run_video_normalisation`` for why.

    Retry policy: OSError gets one retry (transient IO), an ffprobe
    timeout or non-zero exit is permanent and lands on on_failure
    via ``_NormalizeAssetTask``. ``time_limit=120`` is the worst-case
    ffprobe wall-clock (``_FFPROBE_TIMEOUT_S`` is 60 s) doubled.
    """
    asset = processing._row_or_none(asset_id)
    if asset is None:
        return
    processing._run_video_normalisation(asset)
