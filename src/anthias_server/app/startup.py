"""Server-start hooks for anthias-server.

Currently:

* ``run_envelope_check`` — compares the cached playback envelope
  with the one we'd compute for the current ``DEVICE_TYPE``. If
  they differ (or the cache is missing / corrupt), we write the
  fresh value and queue the celery walker to re-render every
  video asset whose recorded ``metadata['envelope']`` no longer
  matches. Cheap when nothing has changed; non-blocking — the
  walker runs in celery, the server keeps serving while it works.

Called from ``AnthiasAppConfig.ready`` so we run exactly once per
server start, after Django has finished wiring up apps but before
any HTTP / WS traffic lands.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def run_envelope_check() -> None:
    """Reconcile the playback envelope cache with what we'd compute
    now, and queue the re-render walker if they differ.

    Failure-mode tolerant:

    * Test runs (``ENVIRONMENT=test`` / ``PYTEST_CURRENT_TEST``)
      short-circuit before touching celery — we don't want every
      ``pytest -m "not integration"`` invocation to enqueue a
      catalog walk against the test SQLite DB.
    * Celery worker connection failures (``redis`` not yet up,
      eager mode mid-test) are logged but don't crash the
      server-start — the walker is a "rebuild stale variants"
      maintenance pass, not a "deny startup until done" gate.
    * ``compute_envelope`` and ``load_cached`` already self-heal
      against bad input (unknown DEVICE_TYPE → default,
      malformed JSON → ``None``), so this function only has to
      glue the pieces together.
    """
    # Skip during test runs — pytest creates a fresh DB per process
    # and we don't want to pollute it (or hit the cache file in the
    # operator's actual ~/.anthias). The test bed exercises the
    # walker behaviour directly via tests/test_celery_tasks.py.
    if os.environ.get('ENVIRONMENT') == 'test':
        return
    if os.environ.get('PYTEST_CURRENT_TEST'):
        return

    # Deferred imports keep the module load light: this file is
    # imported at AppConfig.ready time and shouldn't drag in celery
    # / the asset model on every Django start, just on real ones.
    from anthias_server.playback_envelope import (
        compute_envelope,
        load_cached,
        save_cached,
    )

    try:
        current = compute_envelope()
    except Exception:
        # ``compute_envelope`` itself only reads env + a static
        # dict, so this should be impossible — but the try block
        # is the right place to surface the failure if the matrix
        # ever grows runtime resolution.
        logger.exception(
            'run_envelope_check: compute_envelope() raised; '
            'skipping envelope reconciliation this start'
        )
        return

    cached = load_cached()
    if cached == current:
        logger.debug(
            'run_envelope_check: cached envelope matches current (%s); '
            'no walker needed',
            current.as_dict(),
        )
        return

    # Persist the fresh value first so even if the celery dispatch
    # below fails, the next server start sees the new envelope on
    # disk and reconciles from there. The walker is idempotent
    # (the celery task is the same one the upload path calls), so
    # a missed dispatch this start gets caught on the next.
    try:
        save_cached(current)
    except OSError:
        logger.exception(
            'run_envelope_check: failed to write playback-envelope.json; '
            "the walker still fires but next start won't have the cache"
        )

    if cached is None:
        logger.info(
            'run_envelope_check: no cached envelope; persisting fresh '
            '(%s) and queueing first re-render walker',
            current.as_dict(),
        )
    else:
        logger.info(
            'run_envelope_check: envelope changed (cached=%s, current=%s); '
            'queueing re-render walker',
            cached.as_dict(),
            current.as_dict(),
        )

    try:
        from anthias_server.celery_tasks import regenerate_for_envelope_change

        regenerate_for_envelope_change.delay()
    except Exception:
        # Celery broker not up yet, redis flake, eager-mode quirk —
        # any of these would log + continue. The cache is already
        # saved, so the *next* anthias-server start with celery
        # reachable will see the envelope-as-recorded-on-disk match
        # the computed value and skip re-dispatch. The catalog
        # would still have stale variants in that interval, which
        # the operator can fix by ``celery call`` or by triggering
        # the walker via a future manage.py command.
        logger.exception(
            'run_envelope_check: failed to dispatch '
            'regenerate_for_envelope_change; the walker did NOT run. '
            'Trigger it manually with '
            '`celery -A anthias_server call anthias_server.celery_tasks.'
            'regenerate_for_envelope_change` when celery is reachable.'
        )
