#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import os
from collections.abc import Iterator
from time import monotonic, sleep
from typing import Any
from unittest import mock

import pytest

import anthias_viewer as viewer
from anthias_server.settings import settings
from anthias_viewer.scheduling import Scheduler

logging.disable(logging.CRITICAL)


class _ViewerFixtures:
    u: Any
    m_scheduler: mock.Mock
    p_scheduler: Any
    m_cmd: mock.Mock
    p_cmd: Any
    m_killall: mock.Mock
    p_killall: Any
    m_reload: mock.Mock
    p_reload: Any
    m_sleep: mock.Mock
    p_sleep: Any
    m_loadb: mock.Mock
    p_loadb: Any


@pytest.fixture
def viewer_fixtures() -> Iterator[_ViewerFixtures]:
    fixtures = _ViewerFixtures()
    original_splash_delay = viewer.SPLASH_DELAY
    viewer.SPLASH_DELAY = 0

    fixtures.u = viewer

    fixtures.m_scheduler = mock.Mock(name='m_scheduler')
    fixtures.p_scheduler = mock.patch.object(
        fixtures.u, 'Scheduler', fixtures.m_scheduler
    )

    fixtures.m_cmd = mock.Mock(name='m_cmd')
    fixtures.p_cmd = mock.patch.object(
        fixtures.u.sh, 'Command', fixtures.m_cmd
    )

    fixtures.m_killall = mock.Mock(name='killall')
    fixtures.p_killall = mock.patch.object(
        fixtures.u.sh, 'killall', fixtures.m_killall
    )

    fixtures.m_reload = mock.Mock(name='reload')
    fixtures.p_reload = mock.patch.object(
        fixtures.u, 'load_settings', fixtures.m_reload
    )

    fixtures.m_sleep = mock.Mock(name='sleep')
    fixtures.p_sleep = mock.patch.object(fixtures.u, 'sleep', fixtures.m_sleep)

    fixtures.m_loadb = mock.Mock(name='load_browser')
    fixtures.p_loadb = mock.patch.object(
        fixtures.u, 'load_browser', fixtures.m_loadb
    )

    try:
        yield fixtures
    finally:
        fixtures.u.SPLASH_DELAY = original_splash_delay


def noop(*a: Any, **k: Any) -> None:
    return None


@mock.patch('anthias_viewer.constants.SERVER_WAIT_TIMEOUT', 0)
def test_empty(viewer_fixtures: _ViewerFixtures) -> None:
    m_asset_list = mock.Mock()
    m_asset_list.return_value = ([], None)

    with mock.patch(
        'anthias_viewer.scheduling.generate_asset_list', m_asset_list
    ):
        setattr(viewer_fixtures.u, 'scheduler', Scheduler())

        m_asset_list.assert_called_once()


@mock.patch('pydbus.SessionBus', mock.MagicMock())
def test_setup(viewer_fixtures: _ViewerFixtures) -> None:
    viewer_fixtures.p_loadb.start()
    try:
        viewer_fixtures.u.setup()
    finally:
        viewer_fixtures.p_loadb.stop()


def test_setup_respawns_webview_when_busget_finds_name_gone(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    """The armv7 init crash can strike between the D-Bus handshake
    (which made load_browser() return) and setup()'s bus.get — the
    name is released again and pydbus raises ServiceUnknown. setup()
    must reap, respawn, and retry the bus.get instead of letting the
    error escape main() (Sentry ANTHIAS-3)."""
    proxy = mock.Mock(name='browser_bus_proxy')
    fake_bus = mock.Mock()
    fake_bus.get.side_effect = [
        RuntimeError(
            'GDBus.Error:org.freedesktop.DBus.Error.ServiceUnknown: The '
            'name anthias.viewer was not provided by any .service files'
        ),
        proxy,
    ]
    dead_browser = mock.Mock(name='dead_browser')
    dead_browser.is_alive.return_value = False

    viewer_fixtures.p_loadb.start()
    try:
        with (
            mock.patch('pydbus.SessionBus', mock.Mock(return_value=fake_bus)),
            mock.patch.object(viewer_fixtures.u, 'browser', dead_browser),
        ):
            viewer_fixtures.u.setup()
    finally:
        viewer_fixtures.p_loadb.stop()

    assert fake_bus.get.call_count == 2
    dead_browser.terminate.assert_called_once()
    # Once before bus.get, once for the respawn — both with the
    # generous startup budget (no inline kwargs).
    assert viewer_fixtures.m_loadb.call_count == 2
    assert viewer_fixtures.m_loadb.call_args_list[1] == mock.call()
    assert viewer_fixtures.u.browser_bus is proxy


def test_setup_reraises_unrelated_busget_error(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    """A dead session bus (Disconnected) is not respawn-worthy — the
    container restart has to bring up a whole fresh bus."""
    fake_bus = mock.Mock()
    fake_bus.get.side_effect = RuntimeError(
        'GDBus.Error:org.freedesktop.DBus.Error.Disconnected: '
        'The connection is closed'
    )

    viewer_fixtures.p_loadb.start()
    try:
        with (
            mock.patch('pydbus.SessionBus', mock.Mock(return_value=fake_bus)),
            mock.patch.object(viewer_fixtures.u, 'browser', None),
        ):
            with pytest.raises(RuntimeError, match='Disconnected'):
                viewer_fixtures.u.setup()
    finally:
        viewer_fixtures.p_loadb.stop()

    assert fake_bus.get.call_count == 1
    assert viewer_fixtures.m_loadb.call_count == 1


def _stub_browser_stdout_static(
    browser_proc: mock.Mock,
    value: bytes,
) -> None:
    """
    sh.RunningCommand.process.stdout is a @property that returns the
    latest accumulated buffer on each access. Use PropertyMock so
    the test exercises the same poll-and-decode pattern as the
    production loop. Static variant: every read returns the same
    bytes value, suitable for cases where the loop doesn't depend
    on stdout changing across iterations (early-exit, timeout).
    """
    type(browser_proc.process).stdout = mock.PropertyMock(return_value=value)


def _stub_browser_stdout_chunks(
    browser_proc: mock.Mock,
    chunks: list[bytes],
) -> None:
    """As above, but advance through `chunks` across reads — for
    the success case where the handshake appears in a later poll."""
    type(browser_proc.process).stdout = mock.PropertyMock(side_effect=chunks)


def test_load_browser(viewer_fixtures: _ViewerFixtures) -> None:
    browser_proc = viewer_fixtures.m_cmd.return_value.return_value
    # Two stdout reads: an empty buffer on the first poll, then the
    # handshake line appended on the second. Verifies that the
    # polling loop actually re-reads stdout each iteration.
    _stub_browser_stdout_chunks(
        browser_proc,
        [b'starting up\n', b'starting up\nAnthias service start\n'],
    )
    browser_proc.is_alive.return_value = True
    viewer_fixtures.p_cmd.start()
    viewer_fixtures.p_sleep.start()
    try:
        viewer_fixtures.u.load_browser()
    finally:
        viewer_fixtures.p_sleep.stop()
        viewer_fixtures.p_cmd.stop()
    viewer_fixtures.m_cmd.assert_called_once_with('AnthiasViewer')
    # _bg_exc must stay False: with sh's default (True), a webview crash
    # (or our own SIGTERM on teardown) re-raises the exit error inside
    # sh's daemon monitor thread, where nothing can catch it — every
    # crash the retry loop already handles would still surface as an
    # unhandled-in-thread error in Sentry.
    launch_kwargs = viewer_fixtures.m_cmd.return_value.call_args.kwargs
    assert launch_kwargs['_bg'] is True
    assert launch_kwargs['_bg_exc'] is False


def test_spawn_webview_once_raises_on_early_exit(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    """A process that exits before the handshake surfaces as a
    WebviewLaunchError (a RuntimeError subclass) and does NOT terminate
    (it's already dead)."""
    browser_proc = viewer_fixtures.m_cmd.return_value.return_value
    # The error message also reads stdout, so use the static stub.
    _stub_browser_stdout_static(browser_proc, b'')
    browser_proc.is_alive.return_value = False
    viewer_fixtures.p_cmd.start()
    viewer_fixtures.p_sleep.start()
    try:
        with pytest.raises(viewer_fixtures.u.WebviewLaunchError):
            viewer_fixtures.u._spawn_webview_once(30)
    finally:
        viewer_fixtures.p_sleep.stop()
        viewer_fixtures.p_cmd.stop()
    browser_proc.terminate.assert_not_called()


def test_spawn_webview_once_terminates_on_timeout(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    """When the handshake never arrives, the half-started process is
    torn down (terminate) before the error is raised — otherwise a retry
    would leak a second AnthiasViewer contending for the framebuffer /
    D-Bus name."""
    browser_proc = viewer_fixtures.m_cmd.return_value.return_value
    _stub_browser_stdout_static(browser_proc, b'irrelevant noise')
    # is_alive False so _terminate_webview reaps immediately without a
    # busy-wait; the 0s timeout drives the deadline straight past.
    browser_proc.is_alive.return_value = False
    viewer_fixtures.p_cmd.start()
    viewer_fixtures.p_sleep.start()
    try:
        with pytest.raises(viewer_fixtures.u.WebviewLaunchError):
            viewer_fixtures.u._spawn_webview_once(0)
    finally:
        viewer_fixtures.p_sleep.stop()
        viewer_fixtures.p_cmd.stop()
    browser_proc.terminate.assert_called_once()


def test_spawn_webview_once_missing_binary(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    """A missing AnthiasViewer binary raises WebviewBinaryMissingError
    (permanent), which the retry loop must short-circuit on."""
    viewer_fixtures.m_cmd.side_effect = viewer_fixtures.u.sh.CommandNotFound(
        'AnthiasViewer'
    )
    viewer_fixtures.p_cmd.start()
    try:
        with pytest.raises(viewer_fixtures.u.WebviewBinaryMissingError):
            viewer_fixtures.u._spawn_webview_once(30)
    finally:
        viewer_fixtures.p_cmd.stop()


def test_load_browser_retries_then_succeeds(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    """A board that crashes the webview on the first launches but comes
    up on a later one must self-heal in-process — the whole point of the
    spawn retry (the pi3 Qt5/WebEngine heap-corruption crash). Also
    asserts the backoff actually grows (1s then 2s) rather than hammering."""
    attempts = {'n': 0}

    def fake_spawn(_startup_timeout: float) -> mock.Mock:
        attempts['n'] += 1
        if attempts['n'] < 3:
            raise viewer_fixtures.u.WebviewLaunchError('init crash')
        return mock.Mock(name='browser')

    viewer_fixtures.p_sleep.start()
    try:
        with mock.patch.object(
            viewer_fixtures.u, '_spawn_webview_once', side_effect=fake_spawn
        ):
            viewer_fixtures.u.load_browser()
    finally:
        viewer_fixtures.p_sleep.stop()

    assert attempts['n'] == 3
    assert viewer_fixtures.u.browser is not None
    # The two failed attempts must have slept with growing backoff —
    # guards against a regression that drops the sleep (tight loop).
    backoff_sleeps = [
        c.args[0] for c in viewer_fixtures.m_sleep.call_args_list if c.args
    ]
    assert backoff_sleeps == [1, 2]


def test_load_browser_raises_after_exhausting_retries(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    """When every attempt fails, load_browser still raises — but only
    after spending the retry budget, so the fall-through to a container
    restart is slow, not a tight loop."""
    viewer_fixtures.p_sleep.start()
    try:
        with (
            mock.patch.object(
                viewer_fixtures.u,
                '_spawn_webview_once',
                side_effect=viewer_fixtures.u.WebviewLaunchError('crash'),
            ),
            mock.patch.object(
                viewer_fixtures.u, 'BROWSER_SPAWN_MAX_ATTEMPTS', 4
            ),
        ):
            with pytest.raises(viewer_fixtures.u.WebviewLaunchError):
                viewer_fixtures.u.load_browser()
    finally:
        viewer_fixtures.p_sleep.stop()


def test_load_browser_missing_binary_short_circuits(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    """A permanent failure (missing binary) must NOT consume the retry
    budget — it raises on the first attempt with no backoff."""
    calls = {'n': 0}

    def fake_spawn(_startup_timeout: float) -> mock.Mock:
        calls['n'] += 1
        raise viewer_fixtures.u.WebviewBinaryMissingError('nope')

    viewer_fixtures.p_sleep.start()
    try:
        with mock.patch.object(
            viewer_fixtures.u, '_spawn_webview_once', side_effect=fake_spawn
        ):
            with pytest.raises(viewer_fixtures.u.WebviewBinaryMissingError):
                viewer_fixtures.u.load_browser()
    finally:
        viewer_fixtures.p_sleep.stop()

    assert calls['n'] == 1


def test_load_browser_inline_budget_limits_attempts(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    """The mid-playback respawn path passes a small budget so a
    persistent failure can't freeze the asset_loop for minutes — the
    explicit max_attempts caps the spawn count."""
    calls = {'n': 0}

    def fake_spawn(_startup_timeout: float) -> mock.Mock:
        calls['n'] += 1
        raise viewer_fixtures.u.WebviewLaunchError('crash')

    viewer_fixtures.p_sleep.start()
    try:
        with mock.patch.object(
            viewer_fixtures.u, '_spawn_webview_once', side_effect=fake_spawn
        ):
            with pytest.raises(viewer_fixtures.u.WebviewLaunchError):
                viewer_fixtures.u.load_browser(
                    max_attempts=2, backoff_cap=2, startup_timeout=5
                )
    finally:
        viewer_fixtures.p_sleep.stop()

    assert calls['n'] == 2


def test_view_webpage_arms_reload_interval(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    """``view_webpage`` must always call ``setReloadInterval`` after
    ``loadPage`` — even when the URI is unchanged from the previous
    tick, since an asset edit that flips only the interval (no URI
    change) needs the new value to take effect on the next rotation.
    Feature #2813."""
    fake_bus = mock.Mock()
    fake_browser = mock.Mock()
    fake_browser.is_alive.return_value = True

    with (
        mock.patch.object(viewer_fixtures.u, 'browser_bus', fake_bus),
        mock.patch.object(viewer_fixtures.u, 'browser', fake_browser),
        mock.patch.object(viewer_fixtures.u, 'current_browser_url', None),
    ):
        viewer_fixtures.u.view_webpage('https://example.com', 30)

    fake_bus.loadPage.assert_called_once_with('https://example.com')
    fake_bus.setReloadInterval.assert_called_once_with(30)


def test_view_webpage_default_zero_interval(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    """Splash / fallback callers pass no interval, which becomes 0 —
    the C++ side treats that as "disable the timer", so this is the
    no-auto-refresh contract for legacy code paths."""
    fake_bus = mock.Mock()
    fake_browser = mock.Mock()
    fake_browser.is_alive.return_value = True

    with (
        mock.patch.object(viewer_fixtures.u, 'browser_bus', fake_bus),
        mock.patch.object(viewer_fixtures.u, 'browser', fake_browser),
        mock.patch.object(viewer_fixtures.u, 'current_browser_url', None),
    ):
        viewer_fixtures.u.view_webpage('https://example.com')

    fake_bus.setReloadInterval.assert_called_once_with(0)


@pytest.mark.parametrize(
    'error_message,expected_call_count',
    [
        # An older AnthiasViewer without setReloadInterval raises
        # UnknownMethod — viewer must latch the capability flag off
        # so the next rotation skips the D-Bus hop instead of
        # refilling journald.
        (
            'GDBus.Error:org.freedesktop.DBus.Error.UnknownMethod: '
            "No such method 'setReloadInterval'",
            1,
        ),
        # Transient D-Bus error (bus disconnect, timeout, race during
        # webview restart) must NOT permanently disable auto-refresh:
        # the method exists, the call just failed once. Next rotation
        # retries and the warning is debug-level so journald isn't
        # flooded.
        ('Connection closed by peer', 2),
    ],
    ids=['unknown-method-latches', 'transient-error-retries'],
)
def test_view_webpage_setreloadinterval_failure_modes(
    viewer_fixtures: _ViewerFixtures,
    error_message: str,
    expected_call_count: int,
) -> None:
    fake_bus = mock.Mock()
    fake_bus.setReloadInterval.side_effect = RuntimeError(error_message)
    fake_browser = mock.Mock()
    fake_browser.is_alive.return_value = True

    with (
        mock.patch.object(viewer_fixtures.u, 'browser_bus', fake_bus),
        mock.patch.object(viewer_fixtures.u, 'browser', fake_browser),
        mock.patch.object(viewer_fixtures.u, 'current_browser_url', None),
        mock.patch.object(
            viewer_fixtures.u,
            '_webview_supports_set_reload_interval',
            True,
        ),
    ):
        viewer_fixtures.u.view_webpage('https://example.com', 30)
        viewer_fixtures.u.view_webpage('https://example.com', 60)

    assert fake_bus.setReloadInterval.call_count == expected_call_count


def test_load_browser_resets_set_reload_interval_capability(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    """If the webview process crashed and we latched off, the next
    ``load_browser()`` should re-enable capability detection — the
    fresh process might be a newer build that supports the slot,
    and we shouldn't leave auto-refresh permanently disabled because
    the *old* process didn't have it."""
    browser_proc = viewer_fixtures.m_cmd.return_value.return_value
    _stub_browser_stdout_chunks(
        browser_proc,
        [b'Anthias service start\n'],
    )
    browser_proc.is_alive.return_value = True
    viewer_fixtures.p_cmd.start()
    viewer_fixtures.p_sleep.start()
    try:
        with mock.patch.object(
            viewer_fixtures.u,
            '_webview_supports_set_reload_interval',
            False,
        ):
            viewer_fixtures.u.load_browser()
            assert (
                viewer_fixtures.u._webview_supports_set_reload_interval is True
            )
    finally:
        viewer_fixtures.p_sleep.stop()
        viewer_fixtures.p_cmd.stop()


def test_view_webpage_resets_interval_on_unchanged_url(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    """When the URI matches ``current_browser_url`` we skip the
    ``loadPage`` D-Bus call (cheap no-op), but ``setReloadInterval``
    still has to fire so an interval-only edit takes effect without
    a URI change."""
    fake_bus = mock.Mock()
    fake_browser = mock.Mock()
    fake_browser.is_alive.return_value = True
    uri = 'https://example.com'

    with (
        mock.patch.object(viewer_fixtures.u, 'browser_bus', fake_bus),
        mock.patch.object(viewer_fixtures.u, 'browser', fake_browser),
        mock.patch.object(viewer_fixtures.u, 'current_browser_url', uri),
    ):
        viewer_fixtures.u.view_webpage(uri, 90)

    fake_bus.loadPage.assert_not_called()
    fake_bus.setReloadInterval.assert_called_once_with(90)


# What pydbus raises when the webview dies while a call is in flight
# (Sentry 58040ab3 — the post-handshake armv7 heap-corruption crash)
# and when it died before the call and released the bus name.
_NOREPLY_ERROR = (
    'GDBus.Error:org.freedesktop.DBus.Error.NoReply: Message recipient '
    'disconnected from message bus without replying'
)
_SERVICE_UNKNOWN_ERROR = (
    'GDBus.Error:org.freedesktop.DBus.Error.ServiceUnknown: The name '
    'anthias.viewer was not provided by any .service files'
)

_INLINE_BUDGET_KWARGS = {
    'max_attempts': viewer.BROWSER_SPAWN_INLINE_MAX_ATTEMPTS,
    'backoff_cap': viewer.BROWSER_SPAWN_INLINE_BACKOFF_CAP_SECONDS,
    'startup_timeout': viewer.BROWSER_SPAWN_INLINE_TIMEOUT_SECONDS,
}


@pytest.mark.parametrize(
    'error_message',
    [_NOREPLY_ERROR, _SERVICE_UNKNOWN_ERROR],
    ids=['noreply-mid-call', 'service-unknown'],
)
def test_view_image_respawns_webview_on_mid_call_death(
    viewer_fixtures: _ViewerFixtures,
    error_message: str,
) -> None:
    """A webview that survives the handshake but dies during the
    ``loadImage`` D-Bus call must be reaped, respawned with the inline
    budget, and sent the image again — not crash the viewer process
    (Sentry 58040ab3)."""
    uri = 'https://example.com/a.png'
    fake_bus = mock.Mock()
    fake_bus.loadImage.side_effect = [RuntimeError(error_message), None]
    fake_browser = mock.Mock()
    # Alive for view_image's liveness check, gone once
    # _terminate_webview polls it after SIGTERM.
    fake_browser.is_alive.side_effect = [True, False]
    m_load_browser = mock.Mock(name='load_browser')

    with (
        mock.patch.object(viewer_fixtures.u, 'browser_bus', fake_bus),
        mock.patch.object(viewer_fixtures.u, 'browser', fake_browser),
        mock.patch.object(viewer_fixtures.u, 'current_browser_url', None),
        mock.patch.object(viewer_fixtures.u, 'load_browser', m_load_browser),
    ):
        viewer_fixtures.u.view_image(uri)
        # The retried send succeeded, so the latch must reflect it.
        assert viewer_fixtures.u.current_browser_url == uri

    assert fake_bus.loadImage.call_count == 2
    fake_browser.terminate.assert_called_once()
    m_load_browser.assert_called_once_with(**_INLINE_BUDGET_KWARGS)


def test_view_webpage_respawns_webview_on_mid_call_death(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    """Same recovery contract for the ``loadPage`` path — and the
    auto-refresh timer must still be armed on the respawned process."""
    uri = 'https://example.com'
    fake_bus = mock.Mock()
    fake_bus.loadPage.side_effect = [RuntimeError(_NOREPLY_ERROR), None]
    fake_browser = mock.Mock()
    fake_browser.is_alive.side_effect = [True, False]
    m_load_browser = mock.Mock(name='load_browser')

    with (
        mock.patch.object(viewer_fixtures.u, 'browser_bus', fake_bus),
        mock.patch.object(viewer_fixtures.u, 'browser', fake_browser),
        mock.patch.object(viewer_fixtures.u, 'current_browser_url', None),
        mock.patch.object(viewer_fixtures.u, 'load_browser', m_load_browser),
    ):
        viewer_fixtures.u.view_webpage(uri, 30)
        assert viewer_fixtures.u.current_browser_url == uri

    assert fake_bus.loadPage.call_count == 2
    m_load_browser.assert_called_once_with(**_INLINE_BUDGET_KWARGS)
    fake_bus.setReloadInterval.assert_called_once_with(30)


def test_view_image_reraises_unrelated_dbus_error(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    """Method-level failures from a live webview (or a dead session
    bus) are not respawn-worthy — they must propagate so the container
    restart handles what a webview respawn can't fix."""
    fake_bus = mock.Mock()
    fake_bus.loadImage.side_effect = RuntimeError(
        'GDBus.Error:org.freedesktop.DBus.Error.Disconnected: '
        'The connection is closed'
    )
    fake_browser = mock.Mock()
    fake_browser.is_alive.return_value = True
    m_load_browser = mock.Mock(name='load_browser')

    with (
        mock.patch.object(viewer_fixtures.u, 'browser_bus', fake_bus),
        mock.patch.object(viewer_fixtures.u, 'browser', fake_browser),
        mock.patch.object(viewer_fixtures.u, 'current_browser_url', None),
        mock.patch.object(viewer_fixtures.u, 'load_browser', m_load_browser),
    ):
        with pytest.raises(RuntimeError, match='Disconnected'):
            viewer_fixtures.u.view_image('https://example.com/a.png')
        # The failed send must not latch the URL or trigger a respawn.
        assert viewer_fixtures.u.current_browser_url is None

    m_load_browser.assert_not_called()
    fake_browser.terminate.assert_not_called()


def test_send_to_webview_raises_when_retry_also_fails(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    """One respawn-and-retry, then give up: if the freshly spawned
    webview dies mid-call too, the error escapes so the container
    restart remains the last resort."""
    send = mock.Mock(side_effect=RuntimeError(_NOREPLY_ERROR))
    m_load_browser = mock.Mock(name='load_browser')

    with (
        mock.patch.object(viewer_fixtures.u, 'browser', None),
        mock.patch.object(viewer_fixtures.u, 'load_browser', m_load_browser),
    ):
        with pytest.raises(RuntimeError, match='NoReply'):
            viewer_fixtures.u._send_to_webview(send)

    assert send.call_count == 2
    m_load_browser.assert_called_once_with(**_INLINE_BUDGET_KWARGS)


def test_load_browser_resets_current_browser_url(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    """A fresh webview displays nothing, so the previous process's URL
    must not short-circuit the next view_* value comparison — with an
    unchanged URL (single-asset playlist) the respawned webview would
    otherwise stay blank forever."""
    browser_proc = viewer_fixtures.m_cmd.return_value.return_value
    _stub_browser_stdout_chunks(
        browser_proc,
        [b'Anthias service start\n'],
    )
    browser_proc.is_alive.return_value = True
    viewer_fixtures.p_cmd.start()
    viewer_fixtures.p_sleep.start()
    try:
        with mock.patch.object(
            viewer_fixtures.u,
            'current_browser_url',
            'https://example.com/stale.png',
        ):
            viewer_fixtures.u.load_browser()
            assert viewer_fixtures.u.current_browser_url is None
    finally:
        viewer_fixtures.p_sleep.stop()
        viewer_fixtures.p_cmd.stop()


def test_watchdog_should_create_file_if_not_exists(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    try:
        os.remove(viewer_fixtures.u.utils.WATCHDOG_PATH)
    except OSError:
        pass
    viewer_fixtures.u.watchdog()
    assert os.path.exists(viewer_fixtures.u.utils.WATCHDOG_PATH) is True


def test_watchdog_should_update_mtime(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    # for watchdog file creation
    viewer_fixtures.u.watchdog()
    mtime = os.path.getmtime(viewer_fixtures.u.utils.WATCHDOG_PATH)

    # Python is too fast?
    sleep(0.01)

    viewer_fixtures.u.watchdog()
    mtime2 = os.path.getmtime(viewer_fixtures.u.utils.WATCHDOG_PATH)
    assert mtime2 > mtime


# ---------------------------------------------------------------------------
# _asset_is_displayable
# ---------------------------------------------------------------------------
#
# The viewer used to call url_fails(uri) on every play, which blocked
# the loop for 1-15s on streams. Reachability is now owned by the
# server (celery beat sweep + on-demand recheck endpoint), and the
# viewer just consults Asset.is_reachable.


def test_displayable_skip_asset_check_short_circuits() -> None:
    """skip_asset_check=True means the operator opted out of any
    gating — display unconditionally, even if is_reachable=False."""
    asset = {
        'asset_id': 'a',
        'uri': 'http://example.com/x',  # NOSONAR
        'skip_asset_check': True,
        'is_reachable': False,
    }
    assert viewer._asset_is_displayable(asset)


def test_displayable_local_file_existence_check() -> None:
    """Local URIs hit the filesystem directly (cheap, no roundtrip
    to the server's view of the world)."""
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False) as fh:
        local_path = fh.name
    try:
        assert viewer._asset_is_displayable(
            {
                'asset_id': 'a',
                'uri': local_path,
                'skip_asset_check': False,
                'is_reachable': True,
            }
        )
    finally:
        os.unlink(local_path)
    # Same path, file gone.
    assert not viewer._asset_is_displayable(
        {
            'asset_id': 'a',
            'uri': local_path,
            'skip_asset_check': False,
            'is_reachable': True,
        }
    )


def test_displayable_remote_uri_consults_is_reachable() -> None:
    ok = {
        'asset_id': 'a',
        'uri': 'http://example.com/x',  # NOSONAR
        'skip_asset_check': False,
        'is_reachable': True,
    }
    bad = {**ok, 'is_reachable': False}
    assert viewer._asset_is_displayable(ok)
    assert not viewer._asset_is_displayable(bad)


def test_displayable_missing_is_reachable_defaults_to_displayable() -> None:
    """Backstop for legacy rows / serializers that don't include the
    field. Don't silently freeze a playlist on an upgrade."""
    asset = {
        'asset_id': 'a',
        'uri': 'http://example.com/x',  # NOSONAR
        'skip_asset_check': False,
    }
    assert viewer._asset_is_displayable(asset)


# ---------------------------------------------------------------------------
# _trigger_asset_recheck
# ---------------------------------------------------------------------------


def test_trigger_recheck_posts_to_recheck_endpoint() -> None:
    """The viewer's job is to send whatever ``internal_auth_token``
    yields as a header; the HMAC derivation itself is exercised in
    ``lib/internal_auth`` and through the recheck-endpoint tests.
    Mocking the token-derivation here keeps this test independent of
    settings state, which has bitten us under pytest-xdist + Docker
    test-image conftest configurations."""
    from anthias_common.internal_auth import INTERNAL_AUTH_HEADER

    with (
        mock.patch(
            'anthias_viewer.internal_auth_token', return_value='deadbeef'
        ),
        mock.patch('anthias_viewer.requests.post') as m,
    ):
        viewer._trigger_asset_recheck('abc')
    m.assert_called_once()
    url = m.call_args.args[0]
    assert '/api/v2/assets/abc/recheck' in url
    assert m.call_args.kwargs['headers'] == {INTERNAL_AUTH_HEADER: 'deadbeef'}


def test_trigger_recheck_no_op_on_missing_asset_id() -> None:
    with mock.patch('anthias_viewer.requests.post') as m:
        viewer._trigger_asset_recheck(None)
    m.assert_not_called()


def test_trigger_recheck_no_op_when_internal_token_missing() -> None:
    """When ``internal_auth_token`` returns '' (no secret available
    in settings or env), the request would be a guaranteed 403 — so
    the viewer skips it rather than burning an HTTP round-trip."""
    with (
        mock.patch('anthias_viewer.internal_auth_token', return_value=''),
        mock.patch('anthias_viewer.requests.post') as m,
    ):
        viewer._trigger_asset_recheck('abc')
    m.assert_not_called()


def test_trigger_recheck_swallows_request_errors() -> None:
    """Best-effort: a server hiccup must not interrupt the asset loop."""
    import requests as _requests

    with (
        mock.patch(
            'anthias_viewer.requests.post',
            side_effect=_requests.ConnectionError('boom'),
        ),
        mock.patch(
            'anthias_viewer.internal_auth_token', return_value='deadbeef'
        ),
    ):
        # Must not raise.
        viewer._trigger_asset_recheck('abc')


def test_asset_loop_does_not_recheck_missing_local_asset() -> None:
    scheduler = mock.Mock()
    scheduler.get_next_asset.return_value = {
        'asset_id': 'local',
        'name': 'local',
        'uri': '/tmp/anthias-missing-local-asset',
        'mimetype': 'image',
        'duration': 10,
        'skip_asset_check': False,
        'is_reachable': True,
    }
    skip_event = mock.Mock()
    skip_event.wait.return_value = False
    with (
        mock.patch('anthias_viewer._trigger_asset_recheck') as trigger,
        mock.patch('anthias_viewer.get_skip_event', return_value=skip_event),
    ):
        viewer.asset_loop(scheduler)
    trigger.assert_not_called()


def test_asset_loop_rechecks_unreachable_remote_asset() -> None:
    scheduler = mock.Mock()
    scheduler.get_next_asset.return_value = {
        'asset_id': 'remote',
        'name': 'remote',
        'uri': 'https://example.com/offline.png',
        'mimetype': 'image',
        'duration': 10,
        'skip_asset_check': False,
        'is_reachable': False,
    }
    skip_event = mock.Mock()
    skip_event.wait.return_value = False
    with (
        mock.patch('anthias_viewer._trigger_asset_recheck') as trigger,
        mock.patch('anthias_viewer.get_skip_event', return_value=skip_event),
    ):
        viewer.asset_loop(scheduler)
    trigger.assert_called_once_with('remote')


# ---------------------------------------------------------------------------
# _handle_reload / _skip_if_current_asset_inactive — issue #2430
# ---------------------------------------------------------------------------
#
# The viewer used to ignore playlist mutations while an asset was on
# screen — a 1-hour image kept showing for the rest of the hour even
# after delete/deactivate. The ``reload`` command now also signals a
# skip when the displayed asset is no longer active.


def test_handle_reload_runs_load_settings() -> None:
    """``reload`` must still reload settings — that path is exercised
    by the settings.patch() endpoint and predates the skip behaviour."""
    scheduler = mock.Mock()
    scheduler.current_asset_id = None
    with (
        mock.patch.object(viewer, 'scheduler', scheduler),
        mock.patch.object(viewer, 'load_settings') as load,
    ):
        viewer._handle_reload()
    load.assert_called_once()


def test_skip_when_current_asset_deleted() -> None:
    """Deleting the currently-displayed asset must set the skip event."""
    scheduler = mock.Mock()
    scheduler.current_asset_id = 'gone'
    skip_event = mock.Mock()
    with (
        mock.patch.object(viewer, 'scheduler', scheduler),
        mock.patch('anthias_viewer.get_skip_event', return_value=skip_event),
        mock.patch('anthias_viewer.Asset.objects.filter') as objects_filter,
    ):
        # ``filter().first()`` returns None for a deleted row.
        objects_filter.return_value.first.return_value = None
        viewer._skip_if_current_asset_inactive()
    skip_event.set.assert_called_once()


def test_skip_when_current_asset_deactivated() -> None:
    """Toggling is_enabled off on the displayed asset must skip."""
    scheduler = mock.Mock()
    scheduler.current_asset_id = 'asset-1'
    skip_event = mock.Mock()
    inactive_asset = mock.Mock()
    inactive_asset.is_active.return_value = False
    with (
        mock.patch.object(viewer, 'scheduler', scheduler),
        mock.patch('anthias_viewer.get_skip_event', return_value=skip_event),
        mock.patch('anthias_viewer.Asset.objects.filter') as objects_filter,
    ):
        objects_filter.return_value.first.return_value = inactive_asset
        viewer._skip_if_current_asset_inactive()
    skip_event.set.assert_called_once()


def test_no_skip_when_current_asset_still_active() -> None:
    """Unrelated edits (e.g. duration on a different asset) shouldn't
    interrupt the displayed asset."""
    scheduler = mock.Mock()
    scheduler.current_asset_id = 'asset-1'
    skip_event = mock.Mock()
    active_asset = mock.Mock()
    active_asset.is_active.return_value = True
    with (
        mock.patch.object(viewer, 'scheduler', scheduler),
        mock.patch('anthias_viewer.get_skip_event', return_value=skip_event),
        mock.patch('anthias_viewer.Asset.objects.filter') as objects_filter,
    ):
        objects_filter.return_value.first.return_value = active_asset
        viewer._skip_if_current_asset_inactive()
    skip_event.set.assert_not_called()


def test_skip_noop_when_no_current_asset() -> None:
    """Empty playlist → no displayed asset → no DB hit, no skip."""
    scheduler = mock.Mock()
    scheduler.current_asset_id = None
    skip_event = mock.Mock()
    with (
        mock.patch.object(viewer, 'scheduler', scheduler),
        mock.patch('anthias_viewer.get_skip_event', return_value=skip_event),
        mock.patch('anthias_viewer.Asset.objects.filter') as objects_filter,
    ):
        viewer._skip_if_current_asset_inactive()
    objects_filter.assert_not_called()
    skip_event.set.assert_not_called()


def test_skip_noop_before_scheduler_initialised() -> None:
    """A ``reload`` can arrive during the pre-Scheduler wait window
    (``wait_for_server`` etc.) — must not AttributeError."""
    skip_event = mock.Mock()
    with (
        mock.patch.object(viewer, 'scheduler', None),
        mock.patch('anthias_viewer.get_skip_event', return_value=skip_event),
    ):
        # Must not raise.
        viewer._skip_if_current_asset_inactive()
    skip_event.set.assert_not_called()


def test_skip_swallows_db_errors() -> None:
    """A transient DB failure must not interrupt the asset loop — we
    just leave the rotation alone for this tick."""
    scheduler = mock.Mock()
    scheduler.current_asset_id = 'asset-1'
    skip_event = mock.Mock()
    with (
        mock.patch.object(viewer, 'scheduler', scheduler),
        mock.patch('anthias_viewer.get_skip_event', return_value=skip_event),
        mock.patch(
            'anthias_viewer.Asset.objects.filter',
            side_effect=RuntimeError('boom'),
        ),
    ):
        # Must not raise.
        viewer._skip_if_current_asset_inactive()
    skip_event.set.assert_not_called()


# ---------------------------------------------------------------------------
# Screen rotation (issue #2856)


@pytest.fixture
def reset_rotation_state() -> Iterator[None]:
    """_last_applied_rotation + _rotation_bounce_pending are module
    state — snapshot and restore so rotation tests don't bleed into
    each other (or into the prior reload tests that don't mock
    _apply_wlr_transform)."""
    prior_rot = viewer._last_applied_rotation
    prior_url = viewer.current_browser_url
    prior_pending = viewer._rotation_bounce_pending
    try:
        viewer._last_applied_rotation = 0
        viewer._rotation_bounce_pending = False
        yield
    finally:
        viewer._last_applied_rotation = prior_rot
        viewer.current_browser_url = prior_url
        viewer._rotation_bounce_pending = prior_pending


@pytest.mark.parametrize(
    'raw, expected',
    [
        (0, 0),
        (90, 90),
        (180, 180),
        (270, 270),
        ('90', 90),
        # Anything outside the cardinal set collapses to 0 — the v2
        # serializer and form handler already filter on write, but the
        # viewer reads an arbitrary file off disk and must not propagate
        # garbage values (CLI argv for mpv/VLC, env value for Qt) into
        # the playback layer.
        (45, 0),
        (-1, 0),
        ('garbage', 0),
        (None, 0),
    ],
)
def test_rotation_value_clamps(raw: Any, expected: int) -> None:
    with mock.patch.dict(settings, {'screen_rotation': raw}):
        assert viewer._rotation_value() == expected


# ---------------------------------------------------------------------------
# Prefer dark mode — the C++ webview reads ANTHIAS_PREFER_DARK_MODE at
# launch (applyDarkModePreference in src/anthias_webview/src/main.cpp).


@pytest.fixture
def reset_dark_mode_state() -> Iterator[None]:
    """_last_applied_dark_mode + _rotation_bounce_pending are module
    state — snapshot and restore so these tests don't bleed into each
    other or the rotation tests that share the bounce flag."""
    prior_dark = viewer._last_applied_dark_mode
    prior_pending = viewer._rotation_bounce_pending
    try:
        viewer._last_applied_dark_mode = False
        viewer._rotation_bounce_pending = False
        yield
    finally:
        viewer._last_applied_dark_mode = prior_dark
        viewer._rotation_bounce_pending = prior_pending


def test_build_webview_env_sets_dark_mode_flag_when_enabled() -> None:
    with (
        mock.patch.dict(settings, {'prefer_dark_mode': True}),
        mock.patch.dict(
            os.environ, {'QT_QPA_PLATFORM': 'linuxfb'}, clear=False
        ),
    ):
        env = viewer._build_webview_env()
    assert env['ANTHIAS_PREFER_DARK_MODE'] == '1'


def test_build_webview_env_drops_dark_mode_flag_when_disabled() -> None:
    # A stale value inherited from the process env must not leak into the
    # spawned webview when the operator turns the setting back off.
    with (
        mock.patch.dict(settings, {'prefer_dark_mode': False}),
        mock.patch.dict(
            os.environ,
            {'QT_QPA_PLATFORM': 'linuxfb', 'ANTHIAS_PREFER_DARK_MODE': '1'},
            clear=False,
        ),
    ):
        env = viewer._build_webview_env()
    assert 'ANTHIAS_PREFER_DARK_MODE' not in env


def test_handle_reload_queues_bounce_on_dark_mode_change(
    reset_dark_mode_state: None,
) -> None:
    """The dark-mode flag is only read at webview launch, so a live
    toggle has to respawn AnthiasViewer. Like the rotation path,
    _handle_reload runs on the subscriber thread and MUST NOT terminate
    the browser directly — it only latches the new value and sets the
    bounce flag the main thread consumes."""
    fake_browser = mock.Mock()
    skip = mock.Mock()
    with (
        mock.patch.dict(settings, {'prefer_dark_mode': True}),
        mock.patch.object(viewer, 'load_settings'),
        mock.patch.object(viewer, '_maybe_reapply_rotation'),
        mock.patch.object(viewer, '_skip_if_current_asset_inactive'),
        mock.patch.object(viewer, 'browser', fake_browser),
        mock.patch('anthias_viewer.get_skip_event', return_value=skip),
    ):
        viewer._handle_reload()
    fake_browser.terminate.assert_not_called()
    assert viewer._rotation_bounce_pending is True
    skip.set.assert_called_once()
    # Latched immediately so a second `reload` in the gap before the
    # respawn doesn't re-queue another bounce.
    assert viewer._last_applied_dark_mode is True


def test_handle_reload_no_op_when_dark_mode_unchanged(
    reset_dark_mode_state: None,
) -> None:
    skip = mock.Mock()
    viewer._last_applied_dark_mode = True
    with (
        mock.patch.dict(settings, {'prefer_dark_mode': True}),
        mock.patch.object(viewer, 'load_settings'),
        mock.patch.object(viewer, '_maybe_reapply_rotation'),
        mock.patch.object(viewer, '_skip_if_current_asset_inactive'),
        mock.patch('anthias_viewer.get_skip_event', return_value=skip),
    ):
        viewer._handle_reload()
    assert viewer._rotation_bounce_pending is False
    skip.set.assert_not_called()


@pytest.mark.parametrize(
    'qpa, expected',
    [
        # cage + Wayland boards (x86 / arm64 / pi5 per
        # docker/Dockerfile.viewer.j2) — the compositor owns the
        # transform and rotation goes through wlr-randr.
        ('wayland', True),
        # eglfs (pi4-64 / pi3-64) and linuxfb (pi2 / pi3) rotate via a
        # Qt plugin option, NOT wlr-randr.
        ('eglfs', False),
        ('linuxfb', False),
        ('linuxfb:rotation=90', False),
        # Defensive: an unset QPA must not be mistaken for Wayland.
        ('', False),
    ],
)
def test_is_wayland_board_keys_off_qpa(qpa: str, expected: bool) -> None:
    """Issue #3044: _is_wayland_board() must recognise every cage board
    (x86, arm64, pi5), not just x86. Keying off QT_QPA_PLATFORM mirrors
    the Dockerfile split so Pi 5 / arm64 route through the wlr-randr
    rotation path instead of silently doing nothing."""
    with mock.patch.dict(os.environ, {'QT_QPA_PLATFORM': qpa}, clear=False):
        assert viewer._is_wayland_board() is expected


def test_is_wayland_board_true_for_pi5() -> None:
    """The concrete regression: a Pi 5 viewer (DEVICE_TYPE=pi5,
    QT_QPA_PLATFORM=wayland from the Dockerfile) is now correctly
    classified as a Wayland board."""
    with mock.patch.dict(
        os.environ,
        {'DEVICE_TYPE': 'pi5', 'QT_QPA_PLATFORM': 'wayland'},
        clear=False,
    ):
        assert viewer._is_wayland_board() is True


def test_build_webview_env_no_op_on_pi5_wayland() -> None:
    """Issue #3044: on Pi 5 the rotation must NOT be appended as a
    ``:rotation=N`` option — the Qt wayland plugin ignores it (that's a
    linuxfb-only option). Rotation is handled by wlr-randr instead, so
    QT_QPA_PLATFORM is left untouched."""
    with (
        mock.patch.dict(settings, {'screen_rotation': 90}),
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'pi5', 'QT_QPA_PLATFORM': 'wayland'},
            clear=False,
        ),
    ):
        env = viewer._build_webview_env()
    assert env['QT_QPA_PLATFORM'] == 'wayland'


def test_apply_wlr_transform_runs_on_pi5() -> None:
    """Issue #3044: the wlr-randr path must actually fire on Pi 5 —
    previously gated behind an x86-only _is_wayland_board(), so the
    rotation menu was a complete no-op there."""

    def _fake_run(argv: Any, **kwargs: Any) -> mock.Mock:
        result = mock.Mock()
        result.returncode = 0
        result.stdout = (
            'HDMI-A-1\n  Enabled: yes\n' if argv == ['wlr-randr'] else ''
        )
        result.stderr = ''
        return result

    with (
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'pi5', 'QT_QPA_PLATFORM': 'wayland'},
            clear=False,
        ),
        mock.patch(
            'anthias_viewer.subprocess.run', side_effect=_fake_run
        ) as run,
    ):
        assert viewer._apply_wlr_transform(90) is True

    transform_calls = [
        c
        for c in run.call_args_list
        if c.args
        and c.args[0][:1] == ['wlr-randr']
        and '--transform' in c.args[0]
    ]
    assert len(transform_calls) == 1
    argv = transform_calls[0].args[0]
    assert argv[argv.index('--transform') + 1] == '90'


def test_build_webview_env_appends_rotation_on_linuxfb() -> None:
    """Pi boards (linuxfb) get the rotation baked into QT_QPA_PLATFORM
    so the Qt plugin rotates the framebuffer for free."""
    with (
        mock.patch.dict(settings, {'screen_rotation': 90}),
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'pi3', 'QT_QPA_PLATFORM': 'linuxfb'},
            clear=False,
        ),
    ):
        env = viewer._build_webview_env()
    assert env['QT_QPA_PLATFORM'] == 'linuxfb:rotation=90'


def test_build_webview_env_strips_existing_rotation_suffix() -> None:
    """If a prior launch (or the Dockerfile) baked in a rotation, the
    helper must not double-append."""
    with (
        mock.patch.dict(settings, {'screen_rotation': 180}),
        mock.patch.dict(
            os.environ,
            {
                'DEVICE_TYPE': 'pi3',
                'QT_QPA_PLATFORM': 'linuxfb:rotation=90',
            },
            clear=False,
        ),
    ):
        env = viewer._build_webview_env()
    assert env['QT_QPA_PLATFORM'] == 'linuxfb:rotation=180'


def test_build_webview_env_no_op_on_wayland() -> None:
    """x86 runs Qt wayland under cage; rotation goes through wlr-randr,
    NOT through the QPA plugin which has no rotation= option."""
    with (
        mock.patch.dict(settings, {'screen_rotation': 90}),
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'x86', 'QT_QPA_PLATFORM': 'wayland'},
            clear=False,
        ),
    ):
        env = viewer._build_webview_env()
    assert env['QT_QPA_PLATFORM'] == 'wayland'


def test_build_webview_env_no_suffix_at_zero_rotation() -> None:
    """Default-orientation displays should stay on the existing
    QT_QPA_PLATFORM string so we don't change behavior for the 99%
    case who never touches the Settings dropdown."""
    with (
        mock.patch.dict(settings, {'screen_rotation': 0}),
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'pi3', 'QT_QPA_PLATFORM': 'linuxfb'},
            clear=False,
        ),
    ):
        env = viewer._build_webview_env()
    assert env['QT_QPA_PLATFORM'] == 'linuxfb'


def test_build_webview_env_preserves_other_qpa_options() -> None:
    """An operator who set QT_QPA_PLATFORM=linuxfb:fb=/dev/fb1 must
    keep that option through a rotation change — Copilot review of
    #2882 flagged that the previous naive split dropped it."""
    with (
        mock.patch.dict(settings, {'screen_rotation': 90}),
        mock.patch.dict(
            os.environ,
            {
                'DEVICE_TYPE': 'pi3',
                'QT_QPA_PLATFORM': 'linuxfb:fb=/dev/fb1,tty=/dev/tty1',
            },
            clear=False,
        ),
    ):
        env = viewer._build_webview_env()
    qpa = env['QT_QPA_PLATFORM']
    plugin, _, opts = qpa.partition(':')
    options = set(opts.split(','))
    assert plugin == 'linuxfb'
    assert options == {'fb=/dev/fb1', 'tty=/dev/tty1', 'rotation=90'}


def test_build_webview_env_removes_stale_rotation_when_dialed_to_zero() -> (
    None
):
    """If a previous launch set rotation=90 in QT_QPA_PLATFORM and the
    operator now picks 0° from the dropdown, the rotation= option
    must come back out — otherwise the screen stays rotated after a
    webview respawn. Copilot review of #2882."""
    with (
        mock.patch.dict(settings, {'screen_rotation': 0}),
        mock.patch.dict(
            os.environ,
            {
                'DEVICE_TYPE': 'pi3',
                'QT_QPA_PLATFORM': 'linuxfb:fb=/dev/fb1,rotation=90',
            },
            clear=False,
        ),
    ):
        env = viewer._build_webview_env()
    assert env['QT_QPA_PLATFORM'] == 'linuxfb:fb=/dev/fb1'


@pytest.mark.parametrize(
    ('rotation', 'expected'),
    [(90, '90'), (180, '180'), (270, '-90')],
)
def test_build_webview_env_sets_eglfs_rotation(
    rotation: int, expected: str
) -> None:
    """Pi 4 runs eglfs, which ignores the linuxfb ``:rotation=N`` plugin
    option (that silent no-op was the 2026.06.0 bug). eglfs reads
    QT_QPA_EGLFS_ROTATION at QPA init instead, so we set that and leave
    QT_QPA_PLATFORM untouched. eglfs only accepts 180/90/-90 — a literal
    270 rotates the content without swapping the screen geometry to
    portrait, rendering everything stretched (issue #2970) — so 270°
    must be emitted as -90."""
    with (
        mock.patch.dict(settings, {'screen_rotation': rotation}),
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'pi4-64', 'QT_QPA_PLATFORM': 'eglfs'},
            clear=False,
        ),
    ):
        env = viewer._build_webview_env()
    assert env['QT_QPA_EGLFS_ROTATION'] == expected
    # The platform string must stay a bare plugin — appending
    # ``:rotation=N`` here is exactly the no-op that broke Pi 4.
    assert env['QT_QPA_PLATFORM'] == 'eglfs'


def test_build_webview_env_eglfs_zero_omits_rotation() -> None:
    """Default orientation must not set QT_QPA_EGLFS_ROTATION at all."""
    with (
        mock.patch.dict(settings, {'screen_rotation': 0}),
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'pi4-64', 'QT_QPA_PLATFORM': 'eglfs'},
            clear=False,
        ),
    ):
        # Guard against a stray value leaking in from the real env.
        os.environ.pop('QT_QPA_EGLFS_ROTATION', None)
        env = viewer._build_webview_env()
    assert 'QT_QPA_EGLFS_ROTATION' not in env
    assert env['QT_QPA_PLATFORM'] == 'eglfs'


def test_build_webview_env_eglfs_clears_stale_rotation() -> None:
    """Dialling back to 0° after a rotated launch must drop a stale
    QT_QPA_EGLFS_ROTATION so the respawned webview un-rotates."""
    with (
        mock.patch.dict(settings, {'screen_rotation': 0}),
        mock.patch.dict(
            os.environ,
            {
                'DEVICE_TYPE': 'pi4-64',
                'QT_QPA_PLATFORM': 'eglfs',
                'QT_QPA_EGLFS_ROTATION': '270',
            },
            clear=False,
        ),
    ):
        env = viewer._build_webview_env()
    assert 'QT_QPA_EGLFS_ROTATION' not in env


def test_apply_wlr_transform_skipped_on_linuxfb() -> None:
    """The wlr-randr binary isn't even shipped on Pi boards — make
    sure we never call it from a non-wayland viewer."""
    with (
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'pi3', 'QT_QPA_PLATFORM': 'linuxfb'},
            clear=False,
        ),
        mock.patch('anthias_viewer.subprocess.run') as run,
    ):
        viewer._apply_wlr_transform(90)
    run.assert_not_called()


def test_apply_wlr_transform_invokes_wlr_randr_per_output() -> None:
    """On x86, list enabled outputs then push the transform to each."""

    def _fake_run(argv: Any, **kwargs: Any) -> mock.Mock:
        result = mock.Mock()
        result.returncode = 0
        # The first call lists outputs; subsequent calls apply.
        if argv == ['wlr-randr']:
            result.stdout = (
                'HDMI-A-1 "Foo Display"\n'
                '  Enabled: yes\n'
                '  Modes:\n'
                'HDMI-A-2 "Bar"\n'
                '  Enabled: yes\n'
            )
        else:
            result.stdout = ''
        result.stderr = ''
        return result

    with (
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'x86', 'QT_QPA_PLATFORM': 'wayland'},
            clear=False,
        ),
        mock.patch(
            'anthias_viewer.subprocess.run', side_effect=_fake_run
        ) as run,
    ):
        viewer._apply_wlr_transform(180)

    transform_calls = [
        c
        for c in run.call_args_list
        if c.args
        and c.args[0][:1] == ['wlr-randr']
        and '--transform' in c.args[0]
    ]
    assert len(transform_calls) == 2
    for call in transform_calls:
        assert '--transform' in call.args[0]
        assert call.args[0][call.args[0].index('--transform') + 1] == '180'


def test_wlr_output_names_skips_disabled_outputs() -> None:
    """``wlr-randr --output X --transform ...`` on a disabled
    connector fails noisily and changes nothing — Copilot review of
    #2882 flagged that we were trying anyway. Parser must drop
    blocks whose ``Enabled:`` line reads ``no``."""

    def _fake_run(argv: Any, **kwargs: Any) -> mock.Mock:
        result = mock.Mock()
        result.returncode = 0
        result.stdout = (
            'HDMI-A-1 "Foo"\n'
            '  Enabled: yes\n'
            '  Modes:\n'
            'HDMI-A-2 "Bar"\n'
            '  Enabled: no\n'
            'DP-1 "Baz"\n'
            '  Enabled: yes\n'
        )
        result.stderr = ''
        return result

    with (
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'x86', 'QT_QPA_PLATFORM': 'wayland'},
            clear=False,
        ),
        mock.patch('anthias_viewer.subprocess.run', side_effect=_fake_run),
    ):
        names = viewer._wlr_output_names()
    assert names == ['HDMI-A-1', 'DP-1']


def test_apply_wlr_transform_logs_warning_on_nonzero_exit(
    caplog: Any,
) -> None:
    """wlr-randr's exit code is informative — cage may not be ready
    yet, the output name can vanish between list and apply, etc. The
    helper must surface stderr on failure rather than blanket-logging
    "Applied" on every invocation. Copilot review of #2882."""

    def _fake_run(argv: Any, **kwargs: Any) -> mock.Mock:
        result = mock.Mock()
        if argv == ['wlr-randr']:
            result.returncode = 0
            result.stdout = 'HDMI-A-1\n  Enabled: yes\n'
            result.stderr = ''
        else:
            result.returncode = 1
            result.stdout = ''
            result.stderr = 'invalid output\n'
        return result

    with (
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'x86', 'QT_QPA_PLATFORM': 'wayland'},
            clear=False,
        ),
        mock.patch('anthias_viewer.subprocess.run', side_effect=_fake_run),
        caplog.at_level(logging.WARNING, logger='root'),
    ):
        viewer._apply_wlr_transform(90)

    warning_records = [
        r for r in caplog.records if r.levelno >= logging.WARNING
    ]
    assert any('invalid output' in r.getMessage() for r in warning_records)
    assert not any(
        'Applied wlroots transform' in r.getMessage() for r in caplog.records
    )


def test_handle_reload_reapplies_rotation_when_changed(
    reset_rotation_state: None,
) -> None:
    """The reload pub/sub message must re-push wlr-randr when the
    operator changes rotation in Settings — that's the whole point of
    issue #2856's UI-driven knob.

    Wayland-side rotation change must NOT call MediaPlayerProxy.reset()
    — mpv's wayland VO inherits the compositor transform, so killing
    the in-flight mpv would just leave the screen black until the
    asset's original duration elapses (Copilot review of #2882).
    """
    with (
        mock.patch.dict(settings, {'screen_rotation': 90}),
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'x86', 'QT_QPA_PLATFORM': 'wayland'},
            clear=False,
        ),
        mock.patch.object(viewer, 'load_settings'),
        mock.patch.object(viewer, '_skip_if_current_asset_inactive'),
        mock.patch('anthias_viewer.MediaPlayerProxy.reset') as reset,
        mock.patch.object(
            viewer, '_apply_wlr_transform', return_value=True
        ) as apply,
    ):
        viewer._handle_reload()
    apply.assert_called_once_with(90)
    reset.assert_not_called()
    assert viewer._last_applied_rotation == 90


def test_handle_reload_resets_media_player_on_linuxfb_rotation_change(
    reset_rotation_state: None,
) -> None:
    """On linuxfb (Pi) the rotation change DOES need MediaPlayerProxy
    reset, because VLC bakes the transform filter into the instance
    at construction. Bound for the pi1/2/3 boards specifically."""
    fake_browser = mock.Mock()
    skip = mock.Mock()
    viewer._rotation_bounce_pending = False
    with (
        mock.patch.dict(settings, {'screen_rotation': 90}),
        mock.patch.dict(os.environ, {'DEVICE_TYPE': 'pi3'}, clear=False),
        mock.patch.object(viewer, 'load_settings'),
        mock.patch.object(viewer, '_skip_if_current_asset_inactive'),
        mock.patch.object(viewer, 'browser', fake_browser),
        mock.patch('anthias_viewer.MediaPlayerProxy.reset') as reset,
        mock.patch('anthias_viewer.get_skip_event', return_value=skip),
    ):
        viewer._handle_reload()
    reset.assert_called_once()


def test_handle_reload_does_not_latch_when_wlr_transform_fails(
    reset_rotation_state: None,
) -> None:
    """Issue #2856 (Copilot review #2): if wlr-randr can't apply the
    transform (cage not ready, no outputs, every output failed), we
    must NOT latch the new rotation as ``applied`` — the next reload
    should retry rather than leaving the display stuck unrotated
    until the user changes the setting again."""
    viewer._last_applied_rotation = 0
    with (
        mock.patch.dict(settings, {'screen_rotation': 90}),
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'x86', 'QT_QPA_PLATFORM': 'wayland'},
            clear=False,
        ),
        mock.patch.object(viewer, 'load_settings'),
        mock.patch.object(viewer, '_skip_if_current_asset_inactive'),
        mock.patch('anthias_viewer.MediaPlayerProxy.reset'),
        mock.patch.object(viewer, '_apply_wlr_transform', return_value=False),
    ):
        viewer._handle_reload()
    # Latch unchanged — next reload retries.
    assert viewer._last_applied_rotation == 0


def test_handle_reload_no_rotation_change_is_no_op(
    reset_rotation_state: None,
) -> None:
    """Most reload traffic (asset edits, etc.) must NOT blank the
    screen — the rotation-change path is keyed on a genuine delta."""
    viewer._last_applied_rotation = 90
    with (
        mock.patch.dict(settings, {'screen_rotation': 90}),
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'x86', 'QT_QPA_PLATFORM': 'wayland'},
            clear=False,
        ),
        mock.patch.object(viewer, 'load_settings'),
        mock.patch.object(viewer, '_skip_if_current_asset_inactive'),
        mock.patch('anthias_viewer.MediaPlayerProxy.reset') as reset,
        mock.patch.object(viewer, '_apply_wlr_transform') as apply,
    ):
        viewer._handle_reload()
    apply.assert_not_called()
    reset.assert_not_called()


def test_handle_reload_queues_bounce_on_linuxfb_rotation_change(
    reset_rotation_state: None,
) -> None:
    """linuxfb only reads :rotation=N at QPA init, so the live-rotation
    path has to bounce AnthiasViewer. _handle_reload runs on the
    subscriber thread and MUST NOT terminate the browser directly —
    that would race a concurrent view_*() call mid-D-Bus on the main
    thread (Copilot review of #2882). It only sets a flag; the main
    thread consumes it via _consume_pending_rotation_bounce()."""
    fake_browser = mock.Mock()
    skip = mock.Mock()
    viewer._rotation_bounce_pending = False
    with (
        mock.patch.dict(settings, {'screen_rotation': 270}),
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'pi3', 'QT_QPA_PLATFORM': 'linuxfb'},
            clear=False,
        ),
        mock.patch.object(viewer, 'load_settings'),
        mock.patch.object(viewer, '_skip_if_current_asset_inactive'),
        mock.patch.object(viewer, 'browser', fake_browser),
        mock.patch.object(viewer, 'MediaPlayerProxy'),
        mock.patch('anthias_viewer.get_skip_event', return_value=skip),
    ):
        viewer._handle_reload()
    # NOT called from the subscriber thread.
    fake_browser.terminate.assert_not_called()
    # Flag set so the next asset_loop tick consumes it on the main
    # thread.
    assert viewer._rotation_bounce_pending is True
    skip.set.assert_called_once()
    # _last_applied_rotation is latched immediately, NOT only after
    # load_browser() respawns the webview. Otherwise a second `reload`
    # arriving in the gap would treat rotation as still-changed and
    # spam terminate() flags on an already-pending bounce.
    assert viewer._last_applied_rotation == 270


def test_consume_pending_rotation_bounce_terminates_browser(
    reset_rotation_state: None,
) -> None:
    """The main-thread half of the handoff: when the flag is set,
    terminate the webview and clear current_browser_url so the next
    view_*() call respawns it via load_browser()."""
    fake_browser = mock.Mock()
    viewer._rotation_bounce_pending = True
    with mock.patch.object(viewer, 'browser', fake_browser):
        viewer._consume_pending_rotation_bounce()
    fake_browser.terminate.assert_called_once()
    assert viewer.current_browser_url is None
    # Flag cleared so a subsequent tick (with no new pending bounce)
    # doesn't terminate the freshly-spawned process.
    assert viewer._rotation_bounce_pending is False


def test_consume_pending_rotation_bounce_no_op_when_flag_clear(
    reset_rotation_state: None,
) -> None:
    """Most ticks have no pending bounce — must not touch the
    browser; otherwise every asset_loop iteration would kill it."""
    fake_browser = mock.Mock()
    viewer._rotation_bounce_pending = False
    with mock.patch.object(viewer, 'browser', fake_browser):
        viewer._consume_pending_rotation_bounce()
    fake_browser.terminate.assert_not_called()


def test_asset_loop_consumes_pending_rotation_bounce(
    reset_rotation_state: None,
) -> None:
    """asset_loop must consume the subscriber-set flag at the top of
    each tick — that's the main-thread side of the cross-thread
    handoff that keeps view_*() and rotation-change from racing on
    ``browser``."""
    fake_scheduler = mock.Mock()
    fake_scheduler.get_next_asset.return_value = None
    skip = mock.Mock()
    skip.wait.return_value = False
    with (
        mock.patch.object(
            viewer, '_consume_pending_rotation_bounce'
        ) as consume,
        mock.patch.object(viewer, '_retry_wayland_rotation_if_pending'),
        mock.patch.object(viewer, 'view_image'),
        mock.patch('anthias_viewer.get_skip_event', return_value=skip),
    ):
        viewer.asset_loop(fake_scheduler)
    consume.assert_called_once()


def test_asset_loop_retries_wayland_rotation(
    reset_rotation_state: None,
) -> None:
    """asset_loop must drive the Wayland startup-failure retry —
    Copilot review of #2882 flagged that without it, an early-boot
    wlr-randr failure (cage's wayland socket not yet up) leaves the
    display unrotated forever, since no reload arrives unattended."""
    fake_scheduler = mock.Mock()
    fake_scheduler.get_next_asset.return_value = None
    skip = mock.Mock()
    skip.wait.return_value = False
    with (
        mock.patch.object(viewer, '_consume_pending_rotation_bounce'),
        mock.patch.object(viewer, '_retry_wayland_rotation_if_pending') as r,
        mock.patch.object(viewer, 'view_image'),
        mock.patch('anthias_viewer.get_skip_event', return_value=skip),
    ):
        viewer.asset_loop(fake_scheduler)
    r.assert_called_once()


def test_retry_wayland_rotation_skips_when_already_applied(
    reset_rotation_state: None,
) -> None:
    """Cheap early-return when rotation is already where it should
    be — otherwise asset_loop would push wlr-randr on every tick."""
    viewer._last_applied_rotation = 90
    with (
        mock.patch.dict(settings, {'screen_rotation': 90}),
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'x86', 'QT_QPA_PLATFORM': 'wayland'},
            clear=False,
        ),
        mock.patch.object(viewer, '_apply_wlr_transform') as apply,
    ):
        viewer._retry_wayland_rotation_if_pending()
    apply.assert_not_called()


def test_retry_wayland_rotation_recovers_from_startup_failure(
    reset_rotation_state: None,
) -> None:
    """Sentinel -1 (set by load_browser when the boot apply failed)
    must trigger a retry. On success the latch advances to the real
    angle so subsequent ticks early-return."""
    viewer._last_applied_rotation = -1
    with (
        mock.patch.dict(settings, {'screen_rotation': 270}),
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'x86', 'QT_QPA_PLATFORM': 'wayland'},
            clear=False,
        ),
        mock.patch.object(
            viewer, '_apply_wlr_transform', return_value=True
        ) as apply,
    ):
        viewer._retry_wayland_rotation_if_pending()
    apply.assert_called_once_with(270)
    assert viewer._last_applied_rotation == 270


def test_retry_wayland_rotation_keeps_sentinel_on_failure(
    reset_rotation_state: None,
) -> None:
    """A second-attempt failure must NOT latch the new angle —
    otherwise we'd silently give up on rotation."""
    viewer._last_applied_rotation = -1
    with (
        mock.patch.dict(settings, {'screen_rotation': 270}),
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'x86', 'QT_QPA_PLATFORM': 'wayland'},
            clear=False,
        ),
        mock.patch.object(viewer, '_apply_wlr_transform', return_value=False),
    ):
        viewer._retry_wayland_rotation_if_pending()
    assert viewer._last_applied_rotation == -1


def test_retry_wayland_rotation_skipped_on_linuxfb(
    reset_rotation_state: None,
) -> None:
    """Linuxfb rotation is applied synchronously at QPA init via
    QT_QPA_PLATFORM, so there's no analogous failure mode — the
    retry helper must short-circuit on Pi boards."""
    viewer._last_applied_rotation = -1
    with (
        mock.patch.dict(settings, {'screen_rotation': 90}),
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'pi3', 'QT_QPA_PLATFORM': 'linuxfb'},
            clear=False,
        ),
        mock.patch.object(viewer, '_apply_wlr_transform') as apply,
    ):
        viewer._retry_wayland_rotation_if_pending()
    apply.assert_not_called()


def test_handle_reload_linuxfb_idempotent_under_repeat(
    reset_rotation_state: None,
) -> None:
    """Two ``reload`` messages back-to-back with the same rotation must
    not flap the pending-bounce flag or set the skip_event twice on
    an already-pending bounce — issue raised by Copilot in review of
    #2882. After the first reload latches the new rotation, the
    second sees it unchanged and short-circuits."""
    fake_browser = mock.Mock()
    skip = mock.Mock()
    viewer._rotation_bounce_pending = False
    with (
        mock.patch.dict(settings, {'screen_rotation': 90}),
        mock.patch.dict(
            os.environ,
            {'DEVICE_TYPE': 'pi3', 'QT_QPA_PLATFORM': 'linuxfb'},
            clear=False,
        ),
        mock.patch.object(viewer, 'load_settings'),
        mock.patch.object(viewer, '_skip_if_current_asset_inactive'),
        mock.patch.object(viewer, 'browser', fake_browser),
        mock.patch.object(viewer, 'MediaPlayerProxy'),
        mock.patch('anthias_viewer.get_skip_event', return_value=skip),
    ):
        viewer._handle_reload()
        viewer._handle_reload()
    fake_browser.terminate.assert_not_called()
    skip.set.assert_called_once()
    assert viewer._rotation_bounce_pending is True


class TestPublishDisplayResolutionOnce:
    """The reporter tick must treat a redis blip as a retryable state
    (warning), not a crash (Sentry ANTHIAS-M / ANTHIAS-H)."""

    @pytest.fixture(autouse=True)
    def _enable_logging(self) -> Iterator[None]:
        # This module calls logging.disable(logging.CRITICAL) at
        # import time, which would make the caplog assertions below
        # vacuous (no records ever emitted). Lift the disable for
        # these tests and restore it afterwards.
        logging.disable(logging.NOTSET)
        try:
            yield
        finally:
            logging.disable(logging.CRITICAL)

    def test_redis_down_logs_warning_not_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import redis.exceptions

        with (
            mock.patch.object(
                viewer, 'detect_screen_resolution', return_value='1920x1080'
            ),
            mock.patch.object(
                viewer.r,
                'set',
                side_effect=redis.exceptions.ConnectionError('refused'),
            ),
            caplog.at_level(logging.WARNING),
        ):
            viewer._publish_display_resolution_once()
        assert any(
            record.levelno == logging.WARNING
            and 'redis unreachable' in record.getMessage()
            for record in caplog.records
        )
        assert all(record.levelno < logging.ERROR for record in caplog.records)

    def test_other_failures_still_log_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with (
            mock.patch.object(
                viewer,
                'detect_screen_resolution',
                side_effect=RuntimeError('boom'),
            ),
            caplog.at_level(logging.WARNING),
        ):
            viewer._publish_display_resolution_once()
        assert any(
            record.levelno == logging.ERROR for record in caplog.records
        )

    def test_writes_resolution_with_ttl(self) -> None:
        with (
            mock.patch.object(
                viewer, 'detect_screen_resolution', return_value='1280x720'
            ),
            mock.patch.object(viewer.r, 'set') as set_mock,
        ):
            viewer._publish_display_resolution_once()
        set_mock.assert_called_once_with(
            viewer.DISPLAY_RESOLUTION_KEY,
            '1280x720',
            ex=viewer.DISPLAY_RESOLUTION_TTL_S,
        )


# ---------------------------------------------------------------------------
# Wayland socket wait (Sentry ANTHIAS-19)
# ---------------------------------------------------------------------------


class TestWaitForWaylandSocket:
    """The webview spawn must not race cage's Wayland socket — a spawn
    before the socket exists dies with 'Failed to create wl_display'
    and wastes a retry attempt (Sentry ANTHIAS-19). The gate is the
    WAYLAND_DISPLAY env cage exports, NOT _is_wayland_board(): the wait
    is needed on any board where cage actually ran, and keying it on the
    concrete socket env keeps it correct regardless of how the board
    helper classifies things."""

    def test_no_op_when_no_socket_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # linuxfb/eglfs board: cage never ran, so WAYLAND_DISPLAY is
        # unset and the wait returns immediately without polling.
        monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
        slept = mock.Mock()
        monkeypatch.setattr(viewer, 'sleep', slept)
        viewer._wait_for_wayland_socket(monotonic() + 5)
        slept.assert_not_called()

    def test_returns_immediately_when_socket_present(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
        monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-1')
        (tmp_path / 'wayland-1').write_text('')
        slept = mock.Mock()
        monkeypatch.setattr(viewer, 'sleep', slept)
        viewer._wait_for_wayland_socket(monotonic() + 5)
        slept.assert_not_called()

    def test_fires_via_env_not_board_helper(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # The wait is decoupled from _is_wayland_board(): even a board the
        # helper does NOT classify as Wayland (QT_QPA_PLATFORM unset here,
        # so the helper returns False) still engages the wait when cage
        # exported WAYLAND_DISPLAY. This is the ANTHIAS-19 guarantee —
        # gating on the concrete socket env, not the board helper, is what
        # kept the Pi 5 covered even while the helper was x86-only.
        monkeypatch.delenv('QT_QPA_PLATFORM', raising=False)
        monkeypatch.setenv('DEVICE_TYPE', 'pi5')
        assert viewer._is_wayland_board() is False
        monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
        monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-1')
        monkeypatch.setattr(viewer, 'sleep', lambda _s: None)
        with caplog.at_level(logging.WARNING):
            # Deadline already passed: the loop body is skipped, but the
            # pre-loop "not present yet" warning still fires iff the wait
            # was entered (i.e. not board-skipped).
            viewer._wait_for_wayland_socket(monotonic() - 1)
        assert any('not present yet' in r.getMessage() for r in caplog.records)

    def test_waits_then_proceeds_when_socket_appears(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
        monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-1')
        socket = tmp_path / 'wayland-1'

        # The socket shows up after the second poll.
        calls = {'n': 0}

        def fake_sleep(_seconds: float) -> None:
            calls['n'] += 1
            if calls['n'] >= 2:
                socket.write_text('')

        monkeypatch.setattr(viewer, 'sleep', fake_sleep)
        viewer._wait_for_wayland_socket(monotonic() + 100)
        assert socket.exists()
        assert calls['n'] >= 2

    def test_bounded_when_socket_never_appears(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        # A dead compositor must not hang the asset_loop thread — once
        # the shared deadline passes the wait falls through and lets the
        # spawn fail the normal way.
        monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
        monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-1')
        monkeypatch.setattr(viewer, 'sleep', lambda _s: None)
        # Deadline already in the past → returns at once, no hang.
        viewer._wait_for_wayland_socket(monotonic() - 1)
        assert not (tmp_path / 'wayland-1').exists()

    def test_skips_when_runtime_dir_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # WAYLAND_DISPLAY set but XDG_RUNTIME_DIR absent → no socket
        # path can be formed, so skip rather than guess.
        monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-1')
        monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
        slept = mock.Mock()
        monkeypatch.setattr(viewer, 'sleep', slept)
        viewer._wait_for_wayland_socket(monotonic() + 5)
        slept.assert_not_called()

    def test_spawn_waits_for_socket_before_launching(
        self, viewer_fixtures: _ViewerFixtures
    ) -> None:
        # _spawn_webview_once must call the wait before it shells out,
        # so a respawn never races cage. Make the spawn fail fast (a
        # missing binary is the cheapest terminal error) and assert
        # the wait ran first.
        order: list[str] = []

        def fake_wait(_deadline: float) -> None:
            order.append('wait')

        def fake_command(*args: Any, **kwargs: Any) -> Any:
            order.append('spawn')
            raise viewer_fixtures.u.sh.CommandNotFound('AnthiasViewer')

        with (
            mock.patch.object(
                viewer_fixtures.u,
                '_wait_for_wayland_socket',
                side_effect=fake_wait,
            ),
            mock.patch.object(
                viewer_fixtures.u.sh, 'Command', side_effect=fake_command
            ),
            pytest.raises(viewer_fixtures.u.WebviewBinaryMissingError),
        ):
            viewer_fixtures.u._spawn_webview_once(1)
        assert order == ['wait', 'spawn']
