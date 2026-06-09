# -*- coding: utf-8 -*-

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from os import getenv, path
from signal import SIGALRM, signal
from time import monotonic, sleep
from typing import Any

import django
import pydbus
import redis.exceptions
import requests
import sh as sh

from anthias_server.settings import LISTEN, PORT, ReplySender, settings
from anthias_viewer.constants import EMPTY_PL_DELAY as EMPTY_PL_DELAY
from anthias_viewer.constants import SERVER_WAIT_TIMEOUT as SERVER_WAIT_TIMEOUT
from anthias_viewer.constants import SPLASH_DELAY as SPLASH_DELAY
from anthias_viewer.constants import SPLASH_PAGE_URL as SPLASH_PAGE_URL
from anthias_viewer.constants import STANDBY_SCREEN as STANDBY_SCREEN
from anthias_viewer import media_player as _media_player_module
from anthias_viewer.media_player import MediaPlayerProxy
from anthias_viewer.playback import (
    navigate_to_asset,
    play_loop,
    skip_asset,
    stop_loop,
)
from anthias_viewer.utils import (
    command_not_found,
    get_skip_event,
    sigalrm,
    wait_for_server,
    watchdog,
)

django.setup()

# Place imports that uses Django in this block.

from anthias_common.internal_auth import INTERNAL_AUTH_HEADER  # noqa: E402
from anthias_common.internal_auth import internal_auth_token  # noqa: E402
from anthias_common.utils import (  # noqa: E402
    clamp_screen_rotation,
    connect_to_redis,
    detect_screen_resolution,
    string_to_bool,
)
from anthias_server.app.models import Asset  # noqa: E402
from anthias_server.app.models import clamp_refresh_interval  # noqa: E402
from anthias_viewer.messaging import ViewerSubscriber  # noqa: E402
from anthias_viewer.scheduling import Scheduler  # noqa: E402


__author__ = 'Screenly, Inc'
__copyright__ = 'Copyright 2012-2026, Screenly, Inc'
__license__ = 'Dual License: GPLv2 and Commercial License'


current_browser_url: str | None = None
# Latched True->False on the first failure of ``setReloadInterval`` —
# version skew between the running viewer and the AnthiasViewer
# binary persists for the lifetime of the viewer process, so once we
# know the slot isn't there we don't need to keep paying the D-Bus
# round-trip or flooding journald with warnings every rotation.
# An operator who upgrades the webview package should restart the
# viewer anyway; that resets the cache.
_webview_supports_set_reload_interval: bool = True
browser: Any = None
loop_is_stopped: bool = False
browser_bus: Any = None
r = connect_to_redis()
reply_sender = ReplySender(r)

HOME: str | None = None

scheduler: Any = None

# Rotation last applied to the display, in degrees (0/90/180/270). On
# linuxfb boards this is what we baked into QT_QPA_PLATFORM the last
# time AnthiasViewer launched; on Wayland boards it's the wlr-randr
# transform we last pushed. ``_handle_reload`` compares this to the
# freshly-loaded ``settings['screen_rotation']`` to decide whether the
# operator changed rotation from the UI and we need to re-apply.
_last_applied_rotation: int = 0

# Cross-thread handoff for the linuxfb rotation-change path. The
# subscriber thread (ViewerSubscriber) runs _handle_reload when a
# `reload` arrives on Redis pub/sub, but ``browser`` and
# ``current_browser_url`` are owned by the main asset_loop thread —
# touching them from the subscriber would race a concurrent
# view_image()/view_webpage() mid-D-Bus call. Instead, the subscriber
# sets this flag and the main thread consumes it at the top of
# asset_loop via _consume_pending_rotation_bounce().
_rotation_bounce_pending: bool = False


def _rotation_value() -> int:
    """Coerce settings['screen_rotation'] to a known cardinal angle.

    Thin wrapper around the shared clamp_screen_rotation() helper —
    keeps the viewer's call sites short while ensuring the allowed
    set (0/90/180/270) lives in exactly one place.
    """
    try:
        raw = settings['screen_rotation']
    except KeyError:
        return 0
    return clamp_screen_rotation(raw)


def _is_wayland_board() -> bool:
    """True when the viewer runs under cage + Wayland (x86, arm64, pi5),
    where the compositor owns the transform and rotation is applied via
    ``wlr-randr`` in ``_apply_wlr_transform()`` rather than through a Qt
    plugin option.

    Keyed off ``QT_QPA_PLATFORM`` — the same signal the
    docker/Dockerfile.viewer.j2 split sets (``wayland`` for those three
    boards, ``eglfs``/``linuxfb`` elsewhere) — so it stays correct
    without a per-board allowlist to maintain. Gating on DEVICE_TYPE=='x86'
    alone misfired on Pi 5 (and generic arm64): rotation became a no-op
    because neither the linuxfb ``:rotation=`` option nor the wlr-randr
    path ran (issue #3044)."""
    return os.environ.get('QT_QPA_PLATFORM', '').startswith('wayland')


def _set_qpa_rotation(qpa: str, rotation: int) -> str:
    """Return ``qpa`` with its ``rotation=`` option set to ``rotation``
    (or removed entirely when ``rotation`` is 0).

    The Qt QPA syntax is ``<plugin>[:opt1=val1,opt2=val2,...]`` — the
    options are comma-separated *after* a single colon. A naive
    ``split(':', 1)[0]`` would discard every other option an operator
    might have set (e.g. ``linuxfb:fb=/dev/fb1,tty=/dev/tty1``), so
    parse the option list and only touch the ``rotation`` entry.

    Doing this unconditionally (even when ``rotation`` is 0) lets the
    operator dial rotation back to 0 from a non-zero setting without
    a leftover ``rotation=N`` suffix from a prior launch sticking
    around — Copilot review of #2882.
    """
    plugin, _, options_str = qpa.partition(':')
    options = [
        opt.strip()
        for opt in options_str.split(',')
        if opt.strip() and not opt.strip().startswith('rotation=')
    ]
    if rotation:
        options.append(f'rotation={rotation}')
    if not options:
        return plugin
    return f'{plugin}:{",".join(options)}'


def _build_webview_env() -> dict[str, str]:
    """Compose the env to pass when spawning AnthiasViewer.

    Rotation is applied differently per platform plugin:

    * Wayland (x86 under cage): the compositor owns transforms, so the
      env is left alone and ``_apply_wlr_transform`` handles rotation
      with ``wlr-randr`` separately.

    * eglfs (pi4-64, pi3-64): the linuxfb ``:rotation=N`` plugin option is NOT
      understood by eglfs (it's silently ignored — issue #2882's
      original code wrongly assumed Pi 4 was still linuxfb, which is
      why the rotation menu was a no-op there). eglfs reads
      ``QT_QPA_EGLFS_ROTATION`` at QPA init and applies the transform
      to every top-level window through its QOpenGLCompositor;
      AnthiasViewer is a QWidget app, so webpages, images and video
      all rotate uniformly — no per-content rotation needed (and the
      old ``video-rotate`` path in media_player must stay off here or
      the video double-rotates).

    * linuxfb (pi2/pi3, Qt5): the plugin reads ``:rotation=N`` from
      QT_QPA_PLATFORM once at QPA init and rotates the framebuffer for
      us at no perf cost.
    """
    env = dict(os.environ)
    rotation = _rotation_value()
    if _is_wayland_board():
        return env
    qpa = env.get('QT_QPA_PLATFORM', 'linuxfb')
    if qpa.partition(':')[0] == 'eglfs':
        if rotation:
            # eglfs only understands 180, 90 and -90. A literal 270
            # hits the "Invalid rotation" default branch in
            # QEglFSScreen::geometry(), so the QOpenGLCompositor still
            # rotates the content but the screen geometry never swaps
            # to portrait — the window lays out landscape and renders
            # stretched (issue #2970). Spell 270° as -90 instead.
            env['QT_QPA_EGLFS_ROTATION'] = str(
                -90 if rotation == 270 else rotation
            )
        else:
            # Drop a stale value so dialling back to 0 actually
            # un-rotates on the respawned process.
            env.pop('QT_QPA_EGLFS_ROTATION', None)
        return env
    env['QT_QPA_PLATFORM'] = _set_qpa_rotation(qpa, rotation)
    return env


def _wlr_transform_value(rotation_deg: int) -> str:
    return {0: 'normal', 90: '90', 180: '180', 270: '270'}.get(
        rotation_deg, 'normal'
    )


def _wlr_output_names() -> list[str]:
    """List *enabled* connector names known to the wlroots compositor.

    ``wlr-randr`` with no args prints one block per output. Each block
    looks like::

        HDMI-A-1 "Foo Display"
          Enabled: yes
          Modes:
            ...
        HDMI-A-2 "Bar"
          Enabled: no
          ...

    The connector name is the first non-whitespace token on the
    block's first line; whether the output is currently active is
    indicated by an indented ``Enabled: yes|no`` line within the
    block. We skip ``Enabled: no`` outputs because ``wlr-randr
    --output X --transform ...`` on a disabled connector fails with a
    warning that adds noise to the journal without changing behaviour
    (Copilot review of #2882). Empty list means nothing connected and
    enabled (or cage isn't running yet) — callers treat that as "no
    rotation to apply" rather than an error.
    """
    try:
        result = subprocess.run(
            ['wlr-randr'],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        logging.debug('wlr-randr unavailable: %s', exc)
        return []
    if result.returncode != 0:
        logging.debug(
            'wlr-randr exit %d: %s', result.returncode, result.stderr
        )
        return []
    names: list[str] = []
    current: str | None = None
    current_enabled: bool | None = None
    for line in result.stdout.splitlines():
        if not line:
            continue
        if not line[0].isspace():
            # New output block. Commit the previous one (if it was
            # enabled — None means we never saw an explicit Enabled:
            # line, which on modern wlr-randr versions means the
            # output is implicitly enabled, so include it as a
            # conservative default).
            if current is not None and current_enabled is not False:
                names.append(current)
            current = line.split()[0]
            current_enabled = None
        else:
            stripped = line.strip()
            if stripped.startswith('Enabled:'):
                _, _, value = stripped.partition(':')
                current_enabled = value.strip().lower() == 'yes'
    if current is not None and current_enabled is not False:
        names.append(current)
    return names


def _apply_wlr_transform(rotation_deg: int) -> bool:
    """Push the requested transform to every wlroots output.

    Returns:
        * True on non-Wayland boards (no-op — the linuxfb path handles
          rotation through QT_QPA_PLATFORM instead, so the caller is
          safe to latch ``_last_applied_rotation``).
        * True on Wayland when at least one output was successfully
          rotated (a partial success — e.g. one output rotated, one
          rejected — still counts so we don't loop forever on a
          malfunctioning secondary connector).
        * False on Wayland when no outputs were listed (cage / the
          wayland socket not ready yet) or every wlr-randr invocation
          failed. Callers must NOT latch ``_last_applied_rotation``
          in that case so the next ``reload`` retries.
    """
    if not _is_wayland_board():
        return True
    transform = _wlr_transform_value(rotation_deg)
    names = _wlr_output_names()
    if not names:
        return False
    any_success = False
    for name in names:
        try:
            result = subprocess.run(
                ['wlr-randr', '--output', name, '--transform', transform],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            logging.warning(
                'wlr-randr --transform failed for %s: %s', name, exc
            )
            continue
        # Don't blanket-log "Applied" on every invocation: cage may not
        # be ready yet at viewer startup, an EDID-renamed output can
        # vanish between list and apply, or wlroots can reject the
        # transform for an output that doesn't support it. Treat
        # returncode==0 as success and surface stderr on failure so a
        # silently-broken rotation is debuggable from journald.
        if result.returncode == 0:
            logging.info(
                'Applied wlroots transform %s to output %s', transform, name
            )
            any_success = True
        else:
            logging.warning(
                'wlr-randr --transform %s on %s exited %d: %s',
                transform,
                name,
                result.returncode,
                (result.stderr or '').strip(),
            )
    return any_success


def send_current_asset_id_to_server(correlation_id: str | None) -> None:
    if not correlation_id:
        logging.warning(
            'current_asset_id command received without a correlation ID; '
            'dropping reply.'
        )
        return

    # `subscriber.start()` runs before `scheduler = Scheduler()` in
    # main(), so a `current_asset_id` command arriving during the
    # `wait_for_server` window would `AttributeError` on
    # `scheduler.current_asset_id`. Reply with `None` instead — the v1
    # endpoint already treats a falsy id as "no current asset" and
    # returns `[]`, which is the correct answer pre-scheduler-init.
    if scheduler is None:
        logging.info(
            'current_asset_id requested before scheduler was ready; '
            'replying with no current asset.'
        )
        reply_sender.send(correlation_id, {'current_asset_id': None})
        return

    reply_sender.send(
        correlation_id, {'current_asset_id': scheduler.current_asset_id}
    )


commands = {
    'next': lambda _: skip_asset(scheduler),
    'previous': lambda _: skip_asset(scheduler, back=True),
    'asset': lambda asset_id: navigate_to_asset(scheduler, asset_id),
    'reload': lambda _: _handle_reload(),
    'stop': lambda _: setattr(
        __import__('__main__'), 'loop_is_stopped', stop_loop(scheduler)
    ),
    'play': lambda _: setattr(
        __import__('__main__'), 'loop_is_stopped', play_loop()
    ),
    'unknown': lambda _: command_not_found(),
    'current_asset_id': lambda corr: send_current_asset_id_to_server(corr),
}


BROWSER_STARTUP_TIMEOUT_SECONDS = 30
BROWSER_HANDSHAKE_LINE = 'Anthias service start'

# AnthiasViewer can abort during Qt/WebEngine initialization and die
# before the D-Bus handshake. The worst offender is the 32-bit (armv7)
# Qt5 build on Raspberry Pi 2/3: Chromium's init intermittently corrupts
# the heap (`malloc(): unaligned tcache chunk detected`), crashing the
# process. The fault is heap-layout-dependent and every spawn is a fresh
# OS process, so a later launch clears it ~10-25% of the time — we retry
# the spawn so a flaky board self-heals within a few attempts instead of
# the exception escaping main() into a tight container restart loop
# (which floods journald and makes no faster progress).
#
# Two budgets, because load_browser() runs in two very different places:
#   * AT STARTUP (setup): nothing is on screen yet, so spend a generous
#     budget bringing the webview up.
#   * MID-PLAYBACK (view_image / view_webpage respawn): this runs on the
#     single asset_loop thread, so a long retry here would FREEZE the
#     whole viewer — no rotations, no skips, no standby, and watchdog()
#     starved (risking a hardware-watchdog reboot). Keep it short and let
#     a persistent failure raise: the container restart then re-rolls
#     from a clean process. Documented in docs/board-enablement.md.
BROWSER_SPAWN_MAX_ATTEMPTS = 30
BROWSER_SPAWN_BACKOFF_CAP_SECONDS = 15
BROWSER_SPAWN_INLINE_MAX_ATTEMPTS = 3
BROWSER_SPAWN_INLINE_BACKOFF_CAP_SECONDS = 2
BROWSER_SPAWN_INLINE_TIMEOUT_SECONDS = 10

# Poll cadence while waiting for a just-spawned process to handshake or
# die. Sub-second so an init crash (which is what we're retrying past) is
# noticed promptly rather than after a full second spent sleeping on an
# already-dead process.
BROWSER_POLL_INTERVAL_SECONDS = 0.25
# Grace period to let a SIGTERM'd webview exit before we SIGKILL it.
BROWSER_TERMINATE_GRACE_SECONDS = 3


class WebviewLaunchError(RuntimeError):
    """A single AnthiasViewer launch exited or never handshook.

    Subclasses ``RuntimeError`` so existing callers (and tests) that
    catch ``RuntimeError`` keep working after the retry wrapper landed.
    """


class WebviewBinaryMissingError(WebviewLaunchError):
    """AnthiasViewer is not on PATH.

    A permanent failure (bad image build / packaging regression), so the
    retry loop must NOT burn its backoff budget on it — surface it at
    once instead of hiding it behind minutes of silence.
    """


def _terminate_webview(proc: Any) -> None:
    """Best-effort: stop an AnthiasViewer process and confirm it's gone.

    SIGTERM, wait up to ``BROWSER_TERMINATE_GRACE_SECONDS``, then SIGKILL
    if still alive. Reaping before a retry matters so the old process has
    released the framebuffer and the ``anthias.viewer`` D-Bus name before
    the next one starts — otherwise the retry contends with a zombie and
    times out too. Never raises.
    """
    try:
        proc.terminate()
    except Exception:
        logging.debug('Could not SIGTERM AnthiasViewer', exc_info=True)
        return
    deadline = monotonic() + BROWSER_TERMINATE_GRACE_SECONDS
    while monotonic() < deadline:
        if not proc.is_alive():
            return
        sleep(BROWSER_POLL_INTERVAL_SECONDS)
    try:
        proc.kill()
    except Exception:
        logging.debug('Could not SIGKILL AnthiasViewer', exc_info=True)


def _wayland_socket_path() -> str | None:
    """Path to cage's Wayland socket, or ``None`` if the env doesn't
    name one.

    cage exports both ``XDG_RUNTIME_DIR`` and ``WAYLAND_DISPLAY`` to
    the viewer before exec'ing it; the socket lives at their join.
    This env signal — not ``_is_wayland_board()`` — is what gates the
    wait: cage sets WAYLAND_DISPLAY and nothing else does, on x86,
    arm64 AND pi5 alike, whereas ``_is_wayland_board()`` is x86-only
    and would skip the wait on exactly the Pi 5 where ANTHIAS-19
    fired. ``WAYLAND_DISPLAY`` may itself be an absolute path (rare),
    used verbatim then. Returns ``None`` when either piece is missing
    — a non-cage (linuxfb/eglfs) board with no socket to wait for.
    """
    wayland_display = getenv('WAYLAND_DISPLAY')
    if not wayland_display:
        return None
    if os.path.isabs(wayland_display):
        return wayland_display
    runtime_dir = getenv('XDG_RUNTIME_DIR')
    if not runtime_dir:
        return None
    return os.path.join(runtime_dir, wayland_display)


def _wait_for_wayland_socket(deadline: float) -> None:
    """Block until cage's Wayland socket exists, until ``deadline``
    (a ``monotonic()`` timestamp).

    No-op on non-cage boards (the env names no socket) and when the
    socket is already up — the common case, returns at once.
    ``deadline`` is the *shared* spawn-attempt budget, so this wait
    and the D-Bus handshake wait that follows together can't exceed
    ``startup_timeout``; a compositor that never returns falls through
    and the spawn fails the normal way rather than hanging the
    asset_loop thread.
    """
    socket_path = _wayland_socket_path()
    if socket_path is None or os.path.exists(socket_path):
        return
    logging.warning(
        'Wayland socket %s not present yet; waiting (within the spawn '
        'budget) before launching the webview',
        socket_path,
    )
    while monotonic() < deadline:
        if os.path.exists(socket_path):
            return
        sleep(BROWSER_POLL_INTERVAL_SECONDS)
    logging.warning(
        'Wayland socket %s still absent; launching anyway (the launch '
        'will fail and retry if cage is truly down)',
        socket_path,
    )


def _spawn_webview_once(startup_timeout: float) -> Any:
    """Spawn AnthiasViewer once and block until it registers on D-Bus.

    Returns the live ``sh`` background command on success. Raises
    ``WebviewBinaryMissingError`` (permanent) if the binary is absent, or
    ``WebviewLaunchError`` (retry-worthy) if the process exits before the
    handshake or fails to emit it within ``startup_timeout``. The matched
    string must stay in lockstep with ``qInfo() << "Anthias service
    start"`` in src/anthias_webview/src/main.cpp.

    ``startup_timeout`` is the total budget for the attempt: on a cage
    board one shared deadline covers both the wait for the Wayland
    socket and the handshake, so the socket wait can't pile on top of
    ``startup_timeout``.
    """
    deadline = monotonic() + startup_timeout
    # On cage boards, don't race the Wayland socket — a spawn before
    # it exists dies instantly with "Failed to create wl_display" and
    # wastes a retry attempt (Sentry ANTHIAS-19). No-op elsewhere and
    # when the socket is already up (the common case).
    _wait_for_wayland_socket(deadline)
    try:
        # _bg_exc=False: with the default (True), sh re-raises the exit
        # error (e.g. SignalException_SIGABRT on a Qt init crash, or
        # SIGTERM from our own _terminate_webview) inside its daemon
        # monitor thread, where nothing can catch it — Sentry then
        # reports an unhandled error for a failure the handshake watch
        # below already detects and the caller's retry loop already
        # handles. We never call .wait(), so no exception is deferred.
        candidate = sh.Command('AnthiasViewer')(
            _bg=True,
            _bg_exc=False,
            _err_to_out=True,
            _env=_build_webview_env(),
        )
    except sh.CommandNotFound as exc:
        raise WebviewBinaryMissingError(
            f'AnthiasViewer binary not found: {exc}'
        ) from exc

    # Reuse the same ``deadline`` the Wayland-socket wait above shares,
    # so the whole attempt — socket wait plus handshake — is bounded by
    # ``startup_timeout`` rather than the two stacking.
    while monotonic() < deadline:
        if BROWSER_HANDSHAKE_LINE in candidate.process.stdout.decode(
            'utf-8', errors='replace'
        ):
            return candidate
        if not candidate.is_alive():
            raise WebviewLaunchError(
                'AnthiasViewer exited before emitting D-Bus handshake; '
                'stdout: '
                + candidate.process.stdout.decode('utf-8', errors='replace')
            )
        sleep(BROWSER_POLL_INTERVAL_SECONDS)

    # Timed out waiting for the handshake. Tear the half-started process
    # down AND confirm it is gone so a retry can't leave two AnthiasViewers
    # contending for the framebuffer / the ``anthias.viewer`` D-Bus name.
    _terminate_webview(candidate)
    raise WebviewLaunchError(
        f'AnthiasViewer did not emit "{BROWSER_HANDSHAKE_LINE}" within '
        f'{startup_timeout:g}s'
    )


def load_browser(
    max_attempts: int | None = None,
    backoff_cap: int | None = None,
    startup_timeout: float | None = None,
) -> None:
    """Launch AnthiasViewer, retrying the spawn past the flaky init crash.

    Defaults use the generous startup budget. Mid-playback respawns
    (view_image / view_webpage) pass the smaller inline budget so a
    persistent failure can't freeze the asset_loop thread for minutes.
    """
    global browser, _webview_supports_set_reload_interval
    global _last_applied_rotation, current_browser_url
    logging.info('Loading browser...')

    if max_attempts is None:
        max_attempts = BROWSER_SPAWN_MAX_ATTEMPTS
    if backoff_cap is None:
        backoff_cap = BROWSER_SPAWN_BACKOFF_CAP_SECONDS
    if startup_timeout is None:
        startup_timeout = BROWSER_STARTUP_TIMEOUT_SECONDS
    # Always make at least one real spawn attempt — a non-positive
    # max_attempts would otherwise skip the loop entirely and raise a
    # confusing "0 attempts; last error: None".
    max_attempts = max(1, max_attempts)
    # Keep the backoff sane for any call site: a cap below 1s would
    # devolve into a tight retry loop, and a negative one would turn
    # ``backoff`` negative on the second retry, making ``sleep()`` raise
    # ValueError and mask the real launch error. A negative timeout is
    # clamped to 0 (an immediate-timeout attempt, same as passing 0).
    backoff_cap = max(1, backoff_cap)
    startup_timeout = max(0.0, startup_timeout)

    # Re-probe the setReloadInterval capability against the freshly
    # launched binary. The flag latches OFF on UnknownMethod, but a
    # webview crash + restart (or an in-place upgrade then process
    # bounce) might bring up a binary that *does* support the slot —
    # we don't want to leave auto-refresh disabled forever in that
    # case. Resetting on every launch keeps the latch tied to the
    # actual running process, not the viewer's lifetime.
    _webview_supports_set_reload_interval = True

    # Apply screen rotation *before* the webview starts so it picks up
    # the rotated geometry on first frame: the wlroots compositor
    # needs the transform set before Qt queries the output size, and
    # the linuxfb plugin reads ``:rotation=N`` once at QPA init. On
    # linuxfb the env-var path is synchronous (the QPA reads it on
    # construction) so we can latch unconditionally. On Wayland, only
    # latch when at least one output rotation actually succeeded —
    # otherwise an early-boot cage-not-ready failure would silently
    # stick at the unrotated state until a setting change.
    rotation = _rotation_value()
    if _is_wayland_board():
        if _apply_wlr_transform(rotation):
            _last_applied_rotation = rotation
        else:
            # Reset to a sentinel that doesn't match any valid angle
            # so the next asset_loop tick (via
            # _retry_wayland_rotation_if_pending) or the next server
            # `reload` retries the apply. -1 is safe because
            # _rotation_value() only returns cardinals.
            _last_applied_rotation = -1
    else:
        _last_applied_rotation = rotation

    # Drop any stale handle from a previous (now-dead) webview so callers
    # and diagnostics never see a live-looking ``browser`` while we are
    # between processes; it is reassigned on a successful spawn below.
    browser = None
    # A freshly spawned webview displays nothing, so whatever URL the
    # *previous* process was showing must not short-circuit the next
    # view_image/view_webpage value comparison — otherwise a webview
    # that crashed mid-asset respawns to a blank screen and (with an
    # unchanged URL, e.g. a single-asset playlist) never gets the
    # loadImage/loadPage re-sent.
    current_browser_url = None

    # Retry the spawn with capped exponential backoff so a board that
    # intermittently crashes during Qt/WebEngine init self-heals on a
    # later launch instead of propagating out into a restart loop.
    # Bounded, throttled logging — the first failure logs its full reason
    # and each retry logs a one-liner (capped by max_attempts), not a
    # per-second flood. A missing binary is permanent and short-circuits.
    last_error: WebviewLaunchError | None = None
    backoff = 1
    for attempt in range(1, max_attempts + 1):
        try:
            browser = _spawn_webview_once(startup_timeout)
        except WebviewBinaryMissingError:
            raise
        except WebviewLaunchError as exc:
            last_error = exc
            if attempt == 1:
                logging.warning(
                    'AnthiasViewer failed to start (attempt %d/%d): %s',
                    attempt,
                    max_attempts,
                    exc,
                )
            if attempt < max_attempts:
                logging.warning(
                    'Retrying AnthiasViewer in %ds (attempt %d/%d)',
                    backoff,
                    attempt,
                    max_attempts,
                )
                sleep(backoff)
                backoff = min(backoff * 2, backoff_cap)
            continue

        if attempt > 1:
            logging.info(
                'AnthiasViewer started on attempt %d/%d',
                attempt,
                max_attempts,
            )
        return

    # Every attempt failed — surface the last error (the caller, and the
    # container restart as a last resort, react to it). Reached only after
    # the backoff budget is spent, so this is slow, not a tight loop.
    raise WebviewLaunchError(
        f'AnthiasViewer did not start after {max_attempts} attempts; '
        f'last error: {last_error}'
    ) from last_error


# D-Bus error codes that mean "the AnthiasViewer process is gone", as
# opposed to a method-level failure from a live process. Matched by
# substring on the exception message — the same convention as the
# UnknownMethod handling in view_webpage — because pydbus surfaces
# GDBus errors as GLib.GError whose .message carries the code, and the
# test environment stubs ``gi`` so the class itself can't be imported
# here for an isinstance check.
#
#   * NoReply — the peer died WHILE our call was in flight ("Message
#     recipient disconnected from message bus without replying"). This
#     is what the post-handshake armv7 heap-corruption crash looks
#     like from the caller's side (Sentry 58040ab3).
#   * ServiceUnknown / NameHasNoOwner — the peer died BEFORE the call
#     and already released the ``anthias.viewer`` name.
#
# Deliberately NOT included: ``Disconnected`` (it means *our* session
# bus connection dropped, e.g. dbus-daemon died — respawning the
# webview can't fix that, so let it escape and have the container
# restart bring up a whole fresh bus).
_WEBVIEW_GONE_DBUS_ERRORS = (
    'org.freedesktop.DBus.Error.NoReply',
    'org.freedesktop.DBus.Error.ServiceUnknown',
    'org.freedesktop.DBus.Error.NameHasNoOwner',
)


def _is_webview_gone_error(exc: Exception) -> bool:
    message = str(exc)
    return any(code in message for code in _WEBVIEW_GONE_DBUS_ERRORS)


def _send_to_webview(send: Callable[[], Any]) -> None:
    """Run a ``browser_bus`` call, respawning the webview if it died
    mid-call.

    The flaky armv7 Qt5 init crash that load_browser() retries past can
    also strike *after* the D-Bus handshake — then the death surfaces
    not as ``browser.is_alive() == False`` (which view_image /
    view_webpage already handle) but as a GError raised out of the
    in-flight ``call_sync``. Without this wrapper that exception
    escapes main(), turning a one-process crash into a container
    restart loop and defeating the spawn-retry machinery entirely.

    On a webview-gone error: reap the dead process, respawn with the
    short inline budget (this runs on the asset_loop thread — see the
    budget rationale above BROWSER_SPAWN_MAX_ATTEMPTS), and retry the
    call once. The pydbus proxy targets the well-known
    ``anthias.viewer`` name, not the dead peer's unique name, so it
    routes to the respawned process without being rebuilt (the same
    assumption the rotation-bounce respawn relies on). A failure of
    the respawn or the retried call still raises — the container
    restart stays the last resort, it just stops being the first.
    """
    try:
        send()
        return
    except Exception as exc:
        if not _is_webview_gone_error(exc):
            raise
        logging.warning(
            'AnthiasViewer died mid D-Bus call; respawning and retrying '
            'once: %s',
            exc,
        )
    # Reap the dead process before respawning so it has released the
    # framebuffer and the ``anthias.viewer`` bus name (no-op if it is
    # already fully gone; never raises).
    if browser is not None:
        _terminate_webview(browser)
    load_browser(
        max_attempts=BROWSER_SPAWN_INLINE_MAX_ATTEMPTS,
        backoff_cap=BROWSER_SPAWN_INLINE_BACKOFF_CAP_SECONDS,
        startup_timeout=BROWSER_SPAWN_INLINE_TIMEOUT_SECONDS,
    )
    send()


def view_webpage(uri: str, reload_interval_s: int = 0) -> None:
    """Display a webpage and arm its per-asset auto-refresh timer.

    ``reload_interval_s`` mirrors ``Asset.metadata['refresh_interval_s']``:
    0 (the default, and the value used for splash / fallback URLs)
    leaves the existing webview without a reload timer; a positive value
    reloads the visible page on that cadence so dashboards / status
    pages stay current. We always re-send setReloadInterval — even
    when the URL is unchanged from the previous tick — so an edit that
    only flips the interval (no URI change) takes effect on the next
    asset_loop iteration.
    """
    global current_browser_url

    if browser is None or not browser.is_alive():
        # Mid-playback respawn on the asset_loop thread: small, short
        # budget so a persistent crash can't freeze the loop for minutes
        # — let it raise and have the container restart re-roll instead.
        load_browser(
            max_attempts=BROWSER_SPAWN_INLINE_MAX_ATTEMPTS,
            backoff_cap=BROWSER_SPAWN_INLINE_BACKOFF_CAP_SECONDS,
            startup_timeout=BROWSER_SPAWN_INLINE_TIMEOUT_SECONDS,
        )
    # ``!=`` (value comparison): an ``is not`` identity check would
    # only short-circuit when the asset_loop happens to pass the same
    # str object on consecutive ticks, which a JSON-reconstructed URL
    # would defeat.
    if current_browser_url != uri:
        _send_to_webview(lambda: browser_bus.loadPage(uri))
        current_browser_url = uri
    # ``setReloadInterval`` is a new D-Bus method. A viewer running
    # against an older AnthiasViewer (version skew across a fleet
    # rollout, where the viewer container has rotated to a newer image
    # but the webview process hasn't been restarted yet) would raise
    # here and abort the asset loop, taking the screen down.
    # Latch the capability flag *only* for "the method doesn't exist"
    # — transient D-Bus failures (bus disconnect, timeout, race during
    # a webview restart) are logged at debug and retried next rotation
    # so they don't permanently disable auto-refresh on a webview
    # that actually supports it.
    global _webview_supports_set_reload_interval
    if _webview_supports_set_reload_interval:
        try:
            browser_bus.setReloadInterval(int(reload_interval_s))
        except Exception as exc:
            message = str(exc)
            # pydbus surfaces missing-slot errors with the D-Bus error
            # code 'org.freedesktop.DBus.Error.UnknownMethod' in the
            # exception message. Match either the code or the human
            # phrasing so we don't miss it across pydbus versions.
            method_missing = (
                'UnknownMethod' in message
                or 'no such method' in message.lower()
            )
            if method_missing:
                _webview_supports_set_reload_interval = False
                logging.warning(
                    'setReloadInterval not supported by webview '
                    '(version skew?); auto-refresh disabled until '
                    'viewer restart: %s',
                    exc,
                )
            else:
                logging.debug(
                    'Transient setReloadInterval failure (will retry '
                    'next rotation): %s',
                    exc,
                )
    logging.info('Current url is {0}'.format(current_browser_url))


def view_image(uri: str) -> None:
    global current_browser_url

    if browser is None or not browser.is_alive():
        # Mid-playback respawn on the asset_loop thread: small, short
        # budget so a persistent crash can't freeze the loop for minutes
        # — let it raise and have the container restart re-roll instead.
        load_browser(
            max_attempts=BROWSER_SPAWN_INLINE_MAX_ATTEMPTS,
            backoff_cap=BROWSER_SPAWN_INLINE_BACKOFF_CAP_SECONDS,
            startup_timeout=BROWSER_SPAWN_INLINE_TIMEOUT_SECONDS,
        )
    # Value comparison (matches view_webpage): an ``is not`` identity
    # check would only short-circuit when the asset_loop happens to
    # pass the same str object on consecutive ticks, which a JSON-
    # reconstructed URL would defeat.
    if current_browser_url != uri:
        _send_to_webview(lambda: browser_bus.loadImage(uri))
        current_browser_url = uri
    logging.info('Current url is {0}'.format(current_browser_url))

    if string_to_bool(getenv('WEBVIEW_DEBUG', '0')):
        logging.info(browser.process.stdout)


def view_video(uri: str, duration: int | str) -> None:
    logging.debug('Displaying video %s for %s ', uri, duration)
    media_player = MediaPlayerProxy.get_instance()

    media_player.set_asset(uri, duration)
    media_player.play()

    view_image('null')

    try:
        skip_event = get_skip_event()
        skip_event.clear()
        if skip_event.wait(timeout=int(duration)):
            logging.info('Skip detected during video playback, stopping video')
            media_player.stop()
        else:
            pass
    except sh.ErrorReturnCode_1:
        logging.info(
            'Resource URI is not correct, remote host is not responding or '
            'request was rejected.'
        )

    media_player.stop()


def load_settings() -> None:
    """
    Load settings and set the log level.
    """
    settings.load()
    logging.getLogger().setLevel(
        logging.DEBUG if settings['debug_logging'] else logging.INFO
    )


def _handle_reload() -> None:
    """Process a ``reload`` message from the server.

    Reloads settings (so a settings.patch() change takes effect
    immediately), re-applies the screen rotation if it changed
    (issue #2856), and then signals a skip when the currently-displayed
    asset has been deleted or deactivated — issue #2430.
    """
    load_settings()
    _maybe_reapply_rotation()
    _skip_if_current_asset_inactive()


def _maybe_reapply_rotation() -> None:
    """Re-apply ``screen_rotation`` when the operator changed it in the UI.

    Two distinct paths because the rotation primitive is platform-
    specific (see issue #2856):

    * Wayland (x86 under cage): push the new transform with
      ``wlr-randr``. The compositor sends a resize event to its
      surfaces; Qt's wayland QPA picks it up and re-lays out the
      webview in-place. No process restart needed.

    * linuxfb (every Pi board): the Qt linuxfb plugin only reads
      ``QT_QPA_PLATFORM=linuxfb:rotation=N`` at QPA init, so a live
      angle change requires a fresh AnthiasViewer process. Terminate
      it; the next ``asset_loop`` tick sees ``browser.is_alive()`` as
      false and calls ``load_browser()``, which spawns it with the
      updated env.

    No-op when the on-disk angle matches what we last applied, so
    unrelated ``reload`` traffic (asset edits, etc.) doesn't blank
    the screen.
    """
    global _last_applied_rotation, _rotation_bounce_pending
    rotation = _rotation_value()
    if rotation == _last_applied_rotation:
        return

    logging.info(
        'Screen rotation changed: %d -> %d',
        _last_applied_rotation,
        rotation,
    )

    # Drop the cached media player on linuxfb only — VLC bakes the
    # transform filter into the instance at construction, so the new
    # angle only takes effect after we re-init.
    #
    # On Wayland (x86) we explicitly DO NOT call reset(): mpv's
    # wayland VO inherits the compositor transform from cage, so the
    # currently-playing video doesn't need to be restarted to honour
    # the new rotation. Calling reset() here would kill mpv mid-play
    # and the main thread's view_video() would sit blocked on the
    # asset's original ``duration`` skip_event with the screen on
    # the 'null' black image (Copilot review of #2882). VLC isn't
    # used on x86 at all (MediaPlayerProxy routes pi4-64/pi5/x86 to
    # MPVMediaPlayer), so there's no transform-filter rebuild reason
    # to reset() on Wayland either.
    if not _is_wayland_board():
        MediaPlayerProxy.reset()

    if _is_wayland_board():
        # Apply via wlr-randr from the subscriber thread directly:
        # wlr-randr is an out-of-process IPC call that doesn't touch
        # any state shared with the main thread. Only latch on
        # success so a transient failure (cage not ready, transient
        # wayland-socket hiccup) leaves us in "still needs to retry"
        # state — the next asset_loop tick (via
        # _retry_wayland_rotation_if_pending) or the next ``reload``
        # (asset edit, recheck, etc.) will see the mismatch and
        # retry. Without this guard a startup race could latch the
        # unrotated state permanently and only a user re-toggle
        # would recover.
        if _apply_wlr_transform(rotation):
            _last_applied_rotation = rotation
        else:
            logging.warning(
                'wlr-randr could not apply rotation %d on any output; '
                'will retry on the next asset_loop tick.',
                rotation,
            )
        return

    # linuxfb path — the webview needs to be respawned with the new
    # QT_QPA_PLATFORM env. browser.terminate() and current_browser_url
    # are owned by the main asset_loop thread (view_image/view_webpage
    # mutate them mid-D-Bus call), so we MUST NOT touch them from this
    # subscriber thread. Instead, latch the new rotation and raise a
    # ``_rotation_bounce_pending`` flag that the main thread consumes
    # via _consume_pending_rotation_bounce() at the top of asset_loop.
    # The skip_event wakes the main thread out of its current sleep so
    # the bounce happens promptly rather than after the current
    # asset's full duration elapses.
    _last_applied_rotation = rotation
    _rotation_bounce_pending = True
    get_skip_event().set()


def _retry_wayland_rotation_if_pending() -> None:
    """Main-thread retry for an unsuccessful Wayland rotation apply.

    load_browser() at viewer startup tries to push the wlr-randr
    transform before AnthiasViewer spawns, but cage may not have
    fully come up at that point — its wayland socket can be missing
    or its compositor not yet listing outputs. The first apply
    returns False, load_browser() latches ``_last_applied_rotation
    = -1`` (sentinel for "needs retry"), and without this helper the
    display would stay unrotated until the operator next changed the
    setting and triggered a `reload` (Copilot review of #2882).

    Called from asset_loop on every tick. The early-return guard
    means once the rotation has actually taken effect we drop back
    to zero overhead. Linuxfb is unaffected (env-var path is
    synchronous at QPA init, so it can't fail half-applied).
    """
    if not _is_wayland_board():
        return
    global _last_applied_rotation
    rotation = _rotation_value()
    if rotation == _last_applied_rotation:
        return
    if _apply_wlr_transform(rotation):
        _last_applied_rotation = rotation


def _consume_pending_rotation_bounce() -> None:
    """Main-thread half of the linuxfb rotation handoff.

    Called from ``asset_loop`` at the top of each tick. If the
    subscriber set ``_rotation_bounce_pending``, terminate the
    AnthiasViewer process here — on the same thread that owns it —
    so the next view_image/view_webpage sees ``browser.is_alive()``
    return false and respawns it via ``load_browser()`` with the
    updated rotation env. Clearing ``current_browser_url`` defeats
    the value-comparison short-circuit so the fresh webview actually
    gets a loadPage/loadImage on its first asset.
    """
    global _rotation_bounce_pending, browser, current_browser_url
    if not _rotation_bounce_pending:
        return
    _rotation_bounce_pending = False
    logging.info('Consuming pending rotation bounce on main thread')
    if browser is not None:
        try:
            browser.terminate()
        except Exception as exc:
            logging.warning(
                'Could not terminate AnthiasViewer for rotation change: %s',
                exc,
            )
    current_browser_url = None


def _skip_if_current_asset_inactive() -> None:
    """Cut short the current rotation if the displayed asset is gone.

    Issue #2430: deleting or deactivating an asset that's currently on
    screen would only take effect after its full ``duration`` elapsed —
    a 1-hour image kept showing for the rest of the hour. The server
    publishes ``reload`` on every mutation; here we check whether the
    asset we're displaying is still active, and pop the ``skip_event``
    if not so ``asset_loop`` advances on the next tick. Playlist
    refresh itself happens inside ``get_next_asset`` via the existing
    ``get_db_mtime`` short-circuit, so we don't touch ``scheduler``
    state from the subscriber thread — we only signal.
    """
    if scheduler is None:
        return
    current_id = scheduler.current_asset_id
    if not current_id:
        return
    try:
        asset = Asset.objects.filter(asset_id=current_id).first()
    except Exception:
        logging.exception(
            'reload: failed to check current asset %s; skipping skip-decision',
            current_id,
        )
        return
    if asset is None or not asset.is_active():
        logging.info(
            'Current asset %s is no longer active; signalling skip',
            current_id,
        )
        get_skip_event().set()


def _asset_is_displayable(asset: dict[str, Any]) -> bool:
    """Decide whether to play an asset this rotation.

    The reachability of remote URLs is owned by the server (a celery
    beat task refreshes ``Asset.is_reachable`` on a 15-min cadence and
    the ``/api/v2/assets/<id>/recheck`` endpoint covers on-demand
    re-validation). The viewer used to call ``url_fails`` itself on
    every play, but ffprobe on streams blocks the loop for up to 15s
    per rotation — so we trust the field instead and let the server
    own that work.

    Local files still get a filesystem check: the asset row's
    ``is_reachable`` is set against the celery worker's view of the
    filesystem, but assetdir is shared by volume so the answer is the
    same. Cheap, no roundtrip, mirrors prior behavior for local files.
    """
    if asset.get('skip_asset_check'):
        return True
    uri = asset.get('uri') or ''
    if _asset_is_local_file(asset):
        return path.isfile(uri)
    # Default to True so a row written before this field existed
    # (or by an older serializer that doesn't set it) doesn't get
    # silently skipped.
    return bool(asset.get('is_reachable', True))


def _asset_is_local_file(asset: dict[str, Any]) -> bool:
    uri = asset.get('uri') or ''
    return uri.startswith('/')


def _trigger_asset_recheck(asset_id: str | None) -> None:
    """Ask the server to re-probe an asset we couldn't display.

    Best-effort: a failure here just means the asset stays marked
    unreachable until the next periodic sweep, which is acceptable.
    The server-side task rate-limits per asset, so spamming this on
    every rotation through an unreachable asset is safe.
    """
    if not asset_id:
        return
    token = internal_auth_token(settings)
    if not token:
        logging.debug(
            'Skipping recheck for %s: internal token unavailable', asset_id
        )
        return
    try:
        # NOSONAR (S5332): viewer talks to anthias-server over plain
        # HTTP per CLAUDE.md (TLS is opt-in via the Caddy sidecar that
        # bin/enable_ssl.sh installs as a compose override). The
        # production compose templates set LISTEN=anthias-server in the
        # viewer container's environment, so this resolves to the
        # in-stack service hostname; the settings.py default of
        # 127.0.0.1 only kicks in for non-compose deployments. Either
        # way the URL never crosses a network boundary on a default
        # deploy.
        response = requests.post(
            f'http://{LISTEN}:{PORT}/api/v2/assets/{asset_id}/recheck',  # NOSONAR
            timeout=2,
            allow_redirects=False,
            headers={INTERNAL_AUTH_HEADER: token},
        )
    except requests.RequestException as e:
        logging.debug('Failed to trigger recheck for %s: %s', asset_id, e)
        return

    if response.status_code != 202:
        # 404 means the row was deleted between scheduler refresh and
        # this call — the recheck is moot. Anything else (a 5xx, or a
        # 401/302 if the endpoint ever gets re-decorated with @authorized)
        # means the recheck didn't actually enqueue. Log at debug so the
        # operator can see the chain is silently broken without spamming
        # the loop on every rotation past the unreachable asset.
        logging.debug(
            'Recheck request for %s returned unexpected status %s',
            asset_id,
            response.status_code,
        )


def asset_loop(scheduler: Any) -> None:
    # Issue #2856 — consume any pending rotation bounce queued by the
    # subscriber thread BEFORE we do anything else this tick. The
    # subscriber can only set the flag (it doesn't own ``browser`` or
    # ``current_browser_url``); the actual terminate + URL reset have
    # to happen on this thread so they don't race a concurrent
    # view_image / view_webpage mid-D-Bus call. The next view_*
    # invocation below will see browser.is_alive()==False and
    # respawn via load_browser() with the updated rotation env.
    _consume_pending_rotation_bounce()

    # Issue #2856 — and retry the Wayland rotation if the boot-time
    # attempt in load_browser() raced cage's wayland-socket setup.
    # Cheap early-return when nothing's pending.
    _retry_wayland_rotation_if_pending()

    asset = scheduler.get_next_asset()

    if asset is None:
        logging.info(
            'Playlist is empty. Sleeping for %s seconds', EMPTY_PL_DELAY
        )
        view_image(STANDBY_SCREEN)
        skip_event = get_skip_event()
        skip_event.clear()
        if skip_event.wait(timeout=EMPTY_PL_DELAY):
            # Skip was triggered, continue immediately to next iteration
            logging.info(
                'Skip detected during empty playlist wait, continuing'
            )
        else:
            # Duration elapsed normally, continue to next iteration
            pass

    elif _asset_is_displayable(asset):
        name, mime, uri = asset['name'], asset['mimetype'], asset['uri']
        logging.info('Showing asset %s (%s)', name, mime)
        logging.debug('Asset URI %s', uri)
        watchdog()

        if 'image' in mime:
            view_image(uri)
        elif 'web' in mime:
            # Per-asset auto-refresh — feature #2813. ``metadata`` is a
            # JSONField (defaults to {}); the column was historically
            # nullable so be defensive. Anything non-int / out-of-range
            # is rejected on write by the v2 serializer + the page-form
            # handler, but a hand-crafted DB row could still slip a
            # garbage value through, so clamp on read here too via the
            # shared helper. The C++ webview's setReloadInterval also
            # clamps, but doing it here means we don't pay the D-Bus
            # round-trip for an obviously bogus value.
            metadata = asset.get('metadata') or {}
            interval = clamp_refresh_interval(
                metadata.get('refresh_interval_s')
            )
            view_webpage(uri, reload_interval_s=interval)
        elif 'video' in mime or 'streaming' in mime:
            # ``'video' or 'streaming' in mime`` parses as ``'video'
            # or ('streaming' in mime)`` — the truthy literal short-
            # circuits and the branch runs for every mimetype, making
            # the ``else: Unknown MimeType`` arm below unreachable.
            view_video(uri, asset['duration'])
        else:
            logging.error('Unknown MimeType %s', mime)

        if 'image' in mime or 'web' in mime:
            duration = int(asset['duration'])
            logging.info('Sleeping for %s', duration)
            skip_event = get_skip_event()
            skip_event.clear()
            if skip_event.wait(timeout=duration):
                # Skip was triggered, continue immediately to next iteration
                logging.info('Skip detected, moving to next asset immediately')
            else:
                # Duration elapsed normally, continue to next asset
                pass

    else:
        logging.info(
            'Asset %s at %s is not available, skipping.',
            asset['name'],
            asset['uri'],
        )
        if not _asset_is_local_file(asset):
            _trigger_asset_recheck(asset.get('asset_id'))
        skip_event = get_skip_event()
        skip_event.clear()
        if skip_event.wait(timeout=0.5):
            # Skip was triggered, continue immediately to next iteration
            logging.info(
                'Skip detected during asset unavailability wait, continuing'
            )
        else:
            # Duration elapsed normally, continue to next iteration
            pass


def setup() -> None:
    global HOME, browser_bus
    HOME = getenv('HOME')
    if not HOME:
        logging.error('No HOME variable')

        # Alternatively, we can raise an Exception using a custom message,
        # or we can create a new class that extends Exception.
        sys.exit(1)

    # Skip event is now handled via threading instead of signals
    signal(SIGALRM, sigalrm)

    load_settings()
    load_browser()

    bus = pydbus.SessionBus()
    try:
        browser_bus = bus.get('anthias.viewer', '/Anthias')
    except Exception as exc:
        # The flaky armv7 Qt5 init crash can strike in the gap between
        # the D-Bus handshake (which made load_browser() return) and
        # this bus.get — the name is already released again, pydbus
        # raises ServiceUnknown, and without this handler the GError
        # escapes main() and turns one process crash into a container
        # restart loop (Sentry ANTHIAS-3). Same webview-gone detection
        # and respawn-then-retry-once contract as _send_to_webview;
        # we're still at startup, so spend the generous budget.
        if not _is_webview_gone_error(exc):
            raise
        logging.warning(
            'AnthiasViewer died between handshake and bus.get; '
            'respawning and retrying once: %s',
            exc,
        )
        if browser is not None:
            _terminate_webview(browser)
        load_browser()
        browser_bus = bus.get('anthias.viewer', '/Anthias')
    # MPVMediaPlayer calls AnthiasViewer's playVideo / stopVideo
    # slots via this same proxy now that video lives in-process
    # (issue #2904). Inject after load_browser so the proxy is
    # already bound to the running AnthiasViewer; load_browser
    # would otherwise race the D-Bus name registration.
    _media_player_module.set_browser_bus(browser_bus)
    # Give the media player the same webview-gone respawn wrapper the
    # image/page paths use, so a video-play D-Bus call hitting a
    # crashed webview self-heals instead of logging ERROR and leaving
    # the screen dark (#3027 / Sentry ANTHIAS-1A).
    _media_player_module.set_send_to_webview(_send_to_webview)


def start_loop() -> None:
    global loop_is_stopped

    logging.debug('Entering infinite loop.')
    while True:
        if loop_is_stopped:
            sleep(0.1)
            continue

        asset_loop(scheduler)


DISPLAY_RESOLUTION_KEY = 'viewer:display_resolution'
DISPLAY_RESOLUTION_INTERVAL_S = 60
DISPLAY_RESOLUTION_TTL_S = 180


def _publish_display_resolution_once() -> None:
    """One reporter tick — detect the resolution and write it to Redis.

    Never raises: the reporter thread must survive any single failed
    tick and try again on the next one.
    """
    try:
        value = detect_screen_resolution()
        if value:
            r.set(
                DISPLAY_RESOLUTION_KEY,
                value,
                ex=DISPLAY_RESOLUTION_TTL_S,
            )
    except redis.exceptions.ConnectionError as exc:
        # Redis being briefly unreachable (container recycle, compose
        # startup before its DNS name resolves) is an expected state,
        # not a crash — the next tick retries and the key's TTL
        # semantics already make the System Info card fall back
        # gracefully. Warning, not exception: an ERROR-level log with
        # a traceback would land in Sentry (ANTHIAS-M / ANTHIAS-H).
        logging.warning(
            'publish_display_resolution skipped, redis unreachable '
            '(will retry): %s',
            exc,
        )
    except Exception:
        logging.exception('publish_display_resolution failed')


def _publish_display_resolution_loop() -> None:
    """Background reporter — write the active display resolution to
    Redis on a 1-minute cadence with a 3-minute TTL.

    The TTL serves as a liveness signal: if the viewer crashes or the
    HDMI output goes away, the key expires and the System Info card
    automatically falls back to the operator-configured resolution
    from anthias.conf rather than showing stale data.
    """
    import threading

    def tick() -> None:
        while True:
            _publish_display_resolution_once()
            sleep(DISPLAY_RESOLUTION_INTERVAL_S)

    t = threading.Thread(
        target=tick, name='display-resolution-reporter', daemon=True
    )
    t.start()


def main() -> None:
    global scheduler

    setup()

    subscriber = ViewerSubscriber(r, commands)
    subscriber.daemon = True
    subscriber.start()

    _publish_display_resolution_loop()

    # This will prevent white screen from happening before showing the
    # splash screen with IP addresses.
    view_image(STANDBY_SCREEN)

    wait_for_server(SERVER_WAIT_TIMEOUT)

    scheduler = Scheduler()

    if settings['show_splash']:
        view_webpage(SPLASH_PAGE_URL)
        sleep(SPLASH_DELAY)

    # We don't want to show splash page if there are active assets but all of
    # them are not available.
    view_image(STANDBY_SCREEN)

    sleep(0.5)

    start_loop()
