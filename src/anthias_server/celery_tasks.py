import logging
import os
import secrets
import time
from datetime import timedelta
from os import getenv, path
from typing import Any

import django
import sh
from celery import Celery
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


@celery.task(time_limit=120)
def probe_video_duration(asset_id: str) -> None:
    """Resolve a freshly uploaded video's length out of band.

    The HTML upload view returns the table partial as soon as the bytes
    are written so the operator isn't held up by ffprobe (which can
    take several seconds on a Pi 1/Zero). The asset is marked
    ``is_processing=True`` while this task is queued; once the probe
    completes the duration is written and the flag is cleared, which
    drops the "Processing" pill on the next 5s table poll.

    On probe failure the row still leaves ``is_processing=False`` so it
    becomes editable; the operator gets a sensible-default duration
    (whatever the upload view seeded) and can adjust manually.
    """
    try:
        asset = Asset.objects.get(asset_id=asset_id)
    except Asset.DoesNotExist:
        return

    if asset.mimetype != 'video' or not asset.uri:
        Asset.objects.filter(asset_id=asset_id).update(is_processing=False)
        return

    duration: int | None = None
    try:
        td = get_video_duration(asset.uri)
        if td is not None:
            duration = max(1, int(td.total_seconds()))
    except Exception:
        logging.exception(
            'probe_video_duration: ffprobe failed for %s', asset_id
        )

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
