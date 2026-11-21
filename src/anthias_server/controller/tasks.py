"""Celery tasks for controller fan-out.

One task per screen, so a slow or offline screen holds up only its own
task and never the web UI or the other screens.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from datetime import datetime, timedelta

from django.db.models import F, Q
from django.utils import timezone

from anthias_server import celery_tasks
from anthias_server.celery_tasks import celery
from anthias_server.controller import service
from anthias_server.controller.models import Node, NodeStatus, PublishState
from anthias_server.controller.push import live_filter

logger = logging.getLogger(__name__)

# A publish to a Pi 3 moved about 3 MB/s in the spike, so a playlist of
# a few large videos takes minutes. The soft limit lets the task record
# a failure instead of being killed silently.
PUBLISH_SOFT_TIME_LIMIT_S = 60 * 30
PUBLISH_TIME_LIMIT_S = PUBLISH_SOFT_TIME_LIMIT_S + 60
COMMAND_SOFT_TIME_LIMIT_S = 90
COMMAND_TIME_LIMIT_S = COMMAND_SOFT_TIME_LIMIT_S + 30
# Expiring requests calls each screen back (best effort, bounded by the
# client's deadline), so allow for a few slow ones.
HOUSEKEEPING_SOFT_TIME_LIMIT_S = 600
HOUSEKEEPING_TIME_LIMIT_S = HOUSEKEEPING_SOFT_TIME_LIMIT_S + 60

# Status polling is serial and slow on purpose: /info takes about a
# second on a busy Pi, and screens should not spend their CPU on us.
# Every five minutes (matches celery_tasks.CONTROLLER_STATUS_INTERVAL_S,
# which schedules it); "Check now" on the Screens page polls on demand.
# The time budget stays under the interval so runs never overlap, and
# is large enough to reach every screen of a small deployment in one
# run even when a few are slow or unreachable.
STATUS_INTERVAL_S = 300
STATUS_SOFT_TIME_LIMIT_S = 240
STATUS_TIME_LIMIT_S = 270
# Polling is the fallback for screens that are not pushing their status
# (an older Anthias, or a network that blocks the connection; see
# .push). Those are polled at most this often; a screen whose push
# connection just closed has last_checked_at cleared and is polled on
# the next run.
FALLBACK_POLL_S = 15 * 60
STATUS_LOCK_KEY = 'controller:status:lock'

# A second publish to the same screen waits for the first rather than
# racing it (both would create the same assets).
PUBLISH_RETRY_S = 15


# Publishes share the one Celery worker with the display on/off
# schedule, the asset probes and the rest; its pool is a process per
# core, so four on a Pi 2/3. "Publish to all" queues one long upload
# per screen, and without a cap they would take every process and hold
# the display schedule back by minutes. At most this many publishes
# run at once (never the whole pool); the others wait in the queue,
# re-checking every PUBLISH_SLOT_RETRY_S, without holding a process.
#
# A cap rather than a queue of their own: a second queue needs a
# second worker (another process tree on a 1 GB Pi) and a change to
# the worker command in every compose file, Balena and dev included.
PUBLISH_MAX_CONCURRENT = 2
PUBLISH_SLOT_RETRY_S = 20
PUBLISH_SLOTS_KEY = 'controller:publish:slots'

# Take a slot if fewer than ARGV[2] are held. Slots are members of a
# sorted set scored by their expiry, so a worker killed mid-publish
# frees its slot when the task's hard time limit has passed.
_SLOT_ACQUIRE_LUA = """
local now = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
  return 0
end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[3]), ARGV[4])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return 1
"""


def _pool_size() -> int:
    """The worker's process count: Celery's default is one per core."""
    configured = celery.conf.get('worker_concurrency')
    return int(configured or os.cpu_count() or 1)


def publish_slots() -> int:
    """How many publishes may run at once on this worker."""
    pool = _pool_size()
    # Leave at least one process for everything else.
    return max(1, min(PUBLISH_MAX_CONCURRENT, pool - 1))


def _take_publish_slot(holder: str) -> bool:
    now = int(time.time())
    return bool(
        celery_tasks.r.eval(
            _SLOT_ACQUIRE_LUA,
            1,
            PUBLISH_SLOTS_KEY,
            now,
            publish_slots(),
            PUBLISH_TIME_LIMIT_S,
            holder,
        )
    )


def _release_publish_slot(holder: str) -> None:
    celery_tasks.r.zrem(PUBLISH_SLOTS_KEY, holder)


def publish_lock_key(node_id: int) -> str:
    return f'controller:publish:{node_id}:lock'


@celery.task(
    soft_time_limit=PUBLISH_SOFT_TIME_LIMIT_S,
    time_limit=PUBLISH_TIME_LIMIT_S,
)
def publish_to_node(node_id: int, actor: str = '') -> str:
    node = Node.objects.filter(pk=node_id).first()
    if node is None:
        return 'screen removed'
    lock_value = secrets.token_hex(8)
    if not _take_publish_slot(lock_value):
        publish_to_node.apply_async(
            (node_id, actor), countdown=PUBLISH_SLOT_RETRY_S
        )
        return 'waiting for a publish slot'
    key = publish_lock_key(node_id)
    if not celery_tasks.r.set(
        key, lock_value, nx=True, ex=PUBLISH_TIME_LIMIT_S
    ):
        _release_publish_slot(lock_value)
        publish_to_node.apply_async(
            (node_id, actor), countdown=PUBLISH_RETRY_S
        )
        return 'waiting for the running publish'
    try:
        return service.publish(node, actor)
    except Exception as exc:
        Node.objects.filter(pk=node_id).update(
            publish_state=PublishState.FAILED,
            publish_detail=f'Publish stopped: {exc}'[:1000],
        )
        service.audit(
            'publish', 'failed', node=node, detail=str(exc), actor=actor
        )
        raise
    finally:
        celery_tasks.r.eval(celery_tasks._LOCK_RELEASE_LUA, 1, key, lock_value)
        _release_publish_slot(lock_value)


@celery.task(
    soft_time_limit=COMMAND_SOFT_TIME_LIMIT_S,
    time_limit=COMMAND_TIME_LIMIT_S,
)
def node_command(node_id: int, command: str, actor: str = '') -> str:
    node = Node.objects.filter(pk=node_id).first()
    if node is None:
        return 'screen removed'
    return service.run_command(node, command, actor)


@celery.task(
    soft_time_limit=COMMAND_SOFT_TIME_LIMIT_S,
    time_limit=COMMAND_TIME_LIMIT_S,
)
def refresh_node(node_id: int) -> str:
    node = Node.objects.filter(pk=node_id).first()
    if node is None:
        return 'screen removed'
    return service.check_node(node).status


def renewal_due(now: datetime) -> Q:
    """Screens whose certificate the controller should renew over REST.

    A screen with a live push connection renews over it once fewer than
    ``pki.RENEW_BEFORE`` days are left; the controller steps in when it
    has no live connection, or when half that margin has passed and it
    still has not.
    """
    from anthias_server.lib import pki

    soon = now + pki.RENEW_BEFORE
    sooner = now + pki.RENEW_BEFORE / 2
    return Q(client_not_after__lt=sooner) | (
        Q(client_not_after__lt=soon) & ~live_filter(now)
    )


@celery.task(
    soft_time_limit=COMMAND_SOFT_TIME_LIMIT_S,
    time_limit=COMMAND_TIME_LIMIT_S,
)
def renew_node_certificate(node_id: int) -> str:
    node = Node.objects.filter(pk=node_id).first()
    if node is None:
        return 'screen removed'
    return service.renew_certificate(node)


@celery.task(
    soft_time_limit=HOUSEKEEPING_SOFT_TIME_LIMIT_S,
    time_limit=HOUSEKEEPING_TIME_LIMIT_S,
)
def controller_housekeeping() -> None:
    """Hourly upkeep, every step best effort.

    * drop join requests nobody approved in time (the join endpoint
      does not, so an anonymous caller never waits on it);
    * prune expired revocations and re-sign the CRL when due;
    * queue a REST renewal for screens whose certificate is due.
    """
    from anthias_server.controller import ca, join

    if not service.controller_enabled():
        return
    try:
        expired = join.expire_requests()
        if expired:
            logger.info('Expired %s join request(s)', expired)
    except Exception:
        logger.exception('Could not expire join requests')
    try:
        ca.refresh_crl()
    except Exception:
        logger.exception('Could not refresh the revocation list')
    now = timezone.now()
    due = (
        Node.objects.exclude(status=NodeStatus.UNAUTHORIZED)
        .exclude(public_key_pem='')
        .filter(client_not_after__isnull=False)
        .filter(renewal_due(now))
        .values_list('pk', flat=True)
    )
    for node_id in due:
        renew_node_certificate.delay(node_id)


@celery.task(
    soft_time_limit=STATUS_SOFT_TIME_LIMIT_S,
    time_limit=STATUS_TIME_LIMIT_S,
)
def refresh_nodes() -> int:
    """Poll the screens that are due, one at a time.

    Screens with a live push connection report on their own and are
    skipped. The rest are polled when their last check is older than
    FALLBACK_POLL_S (or was cleared). "Check now" on the Screens page
    polls every screen regardless (``refresh_node``).
    """
    if not service.controller_enabled():
        return 0
    lock_value = secrets.token_hex(8)
    if not celery_tasks.r.set(
        STATUS_LOCK_KEY, lock_value, nx=True, ex=STATUS_TIME_LIMIT_S
    ):
        return 0
    checked = 0
    try:
        now = timezone.now()
        due = Q(last_checked_at__isnull=True) | Q(
            last_checked_at__lt=now - timedelta(seconds=FALLBACK_POLL_S)
        )
        nodes = (
            Node.objects.exclude(status=NodeStatus.UNAUTHORIZED)
            # Live screens report on their own, unless the controller
            # found it cannot call them: those keep being checked.
            .exclude(live_filter(now) & ~Q(status=NodeStatus.UNREACHABLE))
            .filter(due)
        ).order_by(F('last_checked_at').asc(nulls_first=True), 'pk')
        for node in nodes:
            # Record the attempt before network I/O, so even a check
            # interrupted by the task limit yields to other screens on
            # the next run.
            node.last_checked_at = timezone.now()
            node.save(update_fields=['last_checked_at'])
            service.check_node(node)
            checked += 1
    finally:
        celery_tasks.r.eval(
            celery_tasks._LOCK_RELEASE_LUA, 1, STATUS_LOCK_KEY, lock_value
        )
    return checked
