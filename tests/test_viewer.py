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
    viewer_fixtures.u.setup()
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
