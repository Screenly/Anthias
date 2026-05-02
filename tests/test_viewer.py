#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import os
from collections.abc import Iterator
from time import sleep
from typing import Any
from unittest import mock

import pytest

import viewer
from viewer.scheduling import Scheduler

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


@mock.patch('viewer.constants.SERVER_WAIT_TIMEOUT', 0)
def test_empty(viewer_fixtures: _ViewerFixtures) -> None:
    m_asset_list = mock.Mock()
    m_asset_list.return_value = ([], None)

    with mock.patch('viewer.scheduling.generate_asset_list', m_asset_list):
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
    from lib.internal_auth import INTERNAL_AUTH_HEADER

    with (
        mock.patch('viewer.internal_auth_token', return_value='deadbeef'),
        mock.patch('viewer.requests.post') as m,
    ):
        viewer._trigger_asset_recheck('abc')
    m.assert_called_once()
    url = m.call_args.args[0]
    assert '/api/v2/assets/abc/recheck' in url
    assert m.call_args.kwargs['headers'] == {INTERNAL_AUTH_HEADER: 'deadbeef'}


def test_trigger_recheck_no_op_on_missing_asset_id() -> None:
    with mock.patch('viewer.requests.post') as m:
        viewer._trigger_asset_recheck(None)
    m.assert_not_called()


def test_trigger_recheck_no_op_when_internal_token_missing() -> None:
    """When ``internal_auth_token`` returns '' (no secret available
    in settings or env), the request would be a guaranteed 403 — so
    the viewer skips it rather than burning an HTTP round-trip."""
    with (
        mock.patch('viewer.internal_auth_token', return_value=''),
        mock.patch('viewer.requests.post') as m,
    ):
        viewer._trigger_asset_recheck('abc')
    m.assert_not_called()


def test_trigger_recheck_swallows_request_errors() -> None:
    """Best-effort: a server hiccup must not interrupt the asset loop."""
    import requests as _requests

    with (
        mock.patch(
            'viewer.requests.post',
            side_effect=_requests.ConnectionError('boom'),
        ),
        mock.patch('viewer.internal_auth_token', return_value='deadbeef'),
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
        mock.patch('viewer._trigger_asset_recheck') as trigger,
        mock.patch('viewer.get_skip_event', return_value=skip_event),
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
        mock.patch('viewer._trigger_asset_recheck') as trigger,
        mock.patch('viewer.get_skip_event', return_value=skip_event),
    ):
        viewer.asset_loop(scheduler)
    trigger.assert_called_once_with('remote')
