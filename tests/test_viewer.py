#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import os
import unittest
from time import sleep
from typing import Any

import mock

import viewer
from viewer.scheduling import Scheduler

logging.disable(logging.CRITICAL)


class ViewerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.original_splash_delay = viewer.SPLASH_DELAY
        viewer.SPLASH_DELAY = 0

        self.u = viewer

        self.m_scheduler = mock.Mock(name='m_scheduler')
        self.p_scheduler = mock.patch.object(
            self.u, 'Scheduler', self.m_scheduler
        )

        self.m_cmd = mock.Mock(name='m_cmd')
        self.p_cmd = mock.patch.object(self.u.sh, 'Command', self.m_cmd)

        self.m_killall = mock.Mock(name='killall')
        self.p_killall = mock.patch.object(
            self.u.sh, 'killall', self.m_killall
        )

        self.m_reload = mock.Mock(name='reload')
        self.p_reload = mock.patch.object(
            self.u, 'load_settings', self.m_reload
        )

        self.m_sleep = mock.Mock(name='sleep')
        self.p_sleep = mock.patch.object(self.u, 'sleep', self.m_sleep)

        self.m_loadb = mock.Mock(name='load_browser')
        self.p_loadb = mock.patch.object(self.u, 'load_browser', self.m_loadb)

    def tearDown(self) -> None:
        self.u.SPLASH_DELAY = self.original_splash_delay


def noop(*a: Any, **k: Any) -> None:
    return None


class TestEmptyPl(ViewerTestCase):
    @mock.patch('viewer.constants.SERVER_WAIT_TIMEOUT', 0)
    def test_empty(self) -> None:
        m_asset_list = mock.Mock()
        m_asset_list.return_value = ([], None)

        with mock.patch('viewer.scheduling.generate_asset_list', m_asset_list):
            setattr(self.u, 'scheduler', Scheduler())

            m_asset_list.assert_called_once()


class TestLoadBrowser(ViewerTestCase):
    @mock.patch('pydbus.SessionBus', mock.MagicMock())
    def test_setup(self) -> None:
        self.p_loadb.start()
        self.u.setup()
        self.p_loadb.stop()

    def test_load_browser(self) -> None:
        browser_proc = self.m_cmd.return_value.return_value
        browser_proc.process.stdout = b'Anthias service start'
        browser_proc.is_alive.return_value = True
        self.p_cmd.start()
        self.u.load_browser()
        self.p_cmd.stop()
        self.m_cmd.assert_called_once_with('AnthiasWebview')

    def test_load_browser_raises_when_process_exits_before_handshake(
        self,
    ) -> None:
        browser_proc = self.m_cmd.return_value.return_value
        browser_proc.process.stdout = b''
        browser_proc.is_alive.return_value = False
        self.p_cmd.start()
        try:
            with self.assertRaises(RuntimeError):
                self.u.load_browser()
        finally:
            self.p_cmd.stop()

    def test_load_browser_times_out_when_handshake_never_arrives(
        self,
    ) -> None:
        browser_proc = self.m_cmd.return_value.return_value
        browser_proc.process.stdout = b'irrelevant noise'
        browser_proc.is_alive.return_value = True
        # Three monotonic() reads: deadline init, one loop iteration
        # below the deadline, one above it.
        monotonic_values = iter([0.0, 0.0, 100.0])
        self.p_cmd.start()
        self.p_sleep.start()
        try:
            with mock.patch.object(
                self.u,
                'monotonic',
                side_effect=lambda: next(monotonic_values),
            ):
                with self.assertRaises(TimeoutError):
                    self.u.load_browser()
        finally:
            self.p_sleep.stop()
            self.p_cmd.stop()


class TestWatchdog(ViewerTestCase):
    def test_watchdog_should_create_file_if_not_exists(self) -> None:
        try:
            os.remove(self.u.utils.WATCHDOG_PATH)
        except OSError:
            pass
        self.u.watchdog()
        self.assertEqual(os.path.exists(self.u.utils.WATCHDOG_PATH), True)

    def test_watchdog_should_update_mtime(self) -> None:
        # for watchdog file creation
        self.u.watchdog()
        mtime = os.path.getmtime(self.u.utils.WATCHDOG_PATH)

        # Python is too fast?
        sleep(0.01)

        self.u.watchdog()
        mtime2 = os.path.getmtime(self.u.utils.WATCHDOG_PATH)
        self.assertGreater(mtime2, mtime)
