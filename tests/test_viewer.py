#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import os
from collections.abc import Iterator
from time import sleep
from typing import Any
from unittest import mock

import pytest

import anthias_viewer as viewer
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
    viewer_fixtures.m_cmd.assert_called_once_with('AnthiasWebview')


def test_load_browser_raises_when_process_exits_before_handshake(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    browser_proc = viewer_fixtures.m_cmd.return_value.return_value
    # The error message also reads stdout, so use the static stub
    # that returns the same value on every access rather than a
    # one-shot side_effect.
    _stub_browser_stdout_static(browser_proc, b'')
    browser_proc.is_alive.return_value = False
    viewer_fixtures.p_cmd.start()
    try:
        with pytest.raises(RuntimeError):
            viewer_fixtures.u.load_browser()
    finally:
        viewer_fixtures.p_cmd.stop()


def test_load_browser_times_out_when_handshake_never_arrives(
    viewer_fixtures: _ViewerFixtures,
) -> None:
    browser_proc = viewer_fixtures.m_cmd.return_value.return_value
    _stub_browser_stdout_static(browser_proc, b'irrelevant noise')
    browser_proc.is_alive.return_value = True
    # Three monotonic() reads: deadline init, one loop iteration
    # below the deadline, one above it.
    monotonic_values = iter([0.0, 0.0, 100.0])
    viewer_fixtures.p_cmd.start()
    viewer_fixtures.p_sleep.start()
    try:
        with mock.patch.object(
            viewer_fixtures.u,
            'monotonic',
            side_effect=lambda: next(monotonic_values),
        ):
            with pytest.raises(TimeoutError):
                viewer_fixtures.u.load_browser()
    finally:
        viewer_fixtures.p_sleep.stop()
        viewer_fixtures.p_cmd.stop()


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
        # An older AnthiasWebview without setReloadInterval raises
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
