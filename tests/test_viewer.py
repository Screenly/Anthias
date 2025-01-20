#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import os
import unittest
from time import sleep

import mock

import viewer

logging.disable(logging.CRITICAL)


class ViewerTestCase(unittest.TestCase):
    def setUp(self):
        self.original_splash_delay = viewer.SPLASH_DELAY
        viewer.SPLASH_DELAY = 0

        self.u = viewer

        self.m_scheduler = mock.Mock(name='m_scheduler')
        self.p_scheduler = mock.patch.object(
            self.u, 'Scheduler', self.m_scheduler)

        self.m_cmd = mock.Mock(name='m_cmd')
        self.p_cmd = mock.patch.object(self.u.sh, 'Command', self.m_cmd)

        self.m_killall = mock.Mock(name='killall')
        self.p_killall = mock.patch.object(
            self.u.sh, 'killall', self.m_killall)

        self.m_reload = mock.Mock(name='reload')
        self.p_reload = mock.patch.object(
            self.u, 'load_settings', self.m_reload)

        self.m_sleep = mock.Mock(name='sleep')
        self.p_sleep = mock.patch.object(self.u, 'sleep', self.m_sleep)

        self.m_loadb = mock.Mock(name='load_browser')
        self.p_loadb = mock.patch.object(self.u, 'load_browser', self.m_loadb)

    def tearDown(self):
        self.u.SPLASH_DELAY = self.original_splash_delay


def noop(*a, **k):
    return None


class TestEmptyPl(ViewerTestCase):

    @mock.patch('viewer.SERVER_WAIT_TIMEOUT', 0)
    @mock.patch('viewer.start_loop', side_effect=noop)
    @mock.patch('viewer.view_image', side_effect=noop)
    @mock.patch('viewer.view_webpage', side_effect=noop)
    @mock.patch('viewer.setup', side_effect=noop)
    def test_empty(
        self,
        mock_setup,
        mock_view_webpage,
        mock_view_image,
        mock_start_loop,
    ):
        m_asset_list = mock.Mock()
        m_asset_list.return_value = ([], None)

        with mock.patch.object(self.u, 'generate_asset_list', m_asset_list):
            self.u.main()

            m_asset_list.assert_called_once()
            mock_setup.assert_called_once()
            mock_view_webpage.assert_called_once()
            self.assertEqual(mock_view_image.call_count, 2)
            mock_start_loop.assert_called_once()


class TestLoadBrowser(ViewerTestCase):
    @mock.patch('pydbus.SessionBus', mock.MagicMock())
    def test_setup(self):
        self.p_loadb.start()
        self.u.setup()
        self.p_loadb.stop()

    def test_load_browser(self):
        self.m_cmd.return_value.return_value.process.stdout = (
            b'Screenly service start'
        )
        self.p_cmd.start()
        self.u.load_browser()
        self.p_cmd.stop()
        self.m_cmd.assert_called_once_with('ScreenlyWebview')


class TestSignalHandlers(ViewerTestCase):
    @mock.patch('vlc.Instance', mock.MagicMock())
    @mock.patch(
        'lib.media_player.get_device_type',
        return_value='pi4'
    )
    def test_usr1(self, lookup_mock):
        self.p_killall.start()
        self.assertEqual(None, self.u.sigusr1(None, None))
        self.p_killall.stop()


class TestWatchdog(ViewerTestCase):
    def test_watchdog_should_create_file_if_not_exists(self):
        try:
            os.remove(self.u.WATCHDOG_PATH)
        except OSError:
            pass
        self.u.watchdog()
        self.assertEqual(os.path.exists(self.u.WATCHDOG_PATH), True)

    def test_watchdog_should_update_mtime(self):
        # for watchdog file creation
        self.u.watchdog()
        mtime = os.path.getmtime(self.u.WATCHDOG_PATH)

        # Python is too fast?
        sleep(0.01)

        self.u.watchdog()
        mtime2 = os.path.getmtime(self.u.WATCHDOG_PATH)
        self.assertGreater(mtime2, mtime)
