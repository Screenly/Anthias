#!/usr/bin/env python
# -*- coding: utf-8 -*-

from nose.tools import ok_, eq_
from nose.plugins.attrib import attr
import mock
import unittest
import os
from time import sleep


class ViewerTestCase(unittest.TestCase):
    def setUp(self):
        import viewer

        self.original_splash_delay = viewer.SPLASH_DELAY
        viewer.SPLASH_DELAY = 0

        self.u = viewer

        self.m_scheduler = mock.Mock(name='m_scheduler')
        self.p_scheduler = mock.patch.object(self.u, 'Scheduler', self.m_scheduler)

        self.m_cmd = mock.Mock(name='m_cmd')
        self.p_cmd = mock.patch.object(self.u.sh, 'Command', self.m_cmd)

        self.m_send = mock.Mock(name='m_send')
        self.p_send = mock.patch.object(self.u, 'browser_send', self.m_send)

        self.m_killall = mock.Mock(name='killall')
        self.p_killall = mock.patch.object(self.u.sh, 'killall', self.m_killall)

        self.m_reload = mock.Mock(name='reload')
        self.p_reload = mock.patch.object(self.u, 'load_settings', self.m_reload)

        self.m_sleep = mock.Mock(name='sleep')
        self.p_sleep = mock.patch.object(self.u, 'sleep', self.m_sleep)

        self.m_loadb = mock.Mock(name='load_browser')
        self.p_loadb = mock.patch.object(self.u, 'load_browser', self.m_loadb)

    def tearDown(self):
        self.u.SPLASH_DELAY = self.original_splash_delay


@attr('fixme')
class TestEmptyPl(ViewerTestCase):
    def test_empty(self):
        m_asset_list = mock.Mock()
        m_asset_list.return_value = ([], None)
        with mock.patch.object(self.u, 'generate_asset_list', m_asset_list):
            self.u.main()


class TestBrowserSend(ViewerTestCase):
    def test_send(self):
        self.p_cmd.start()
        self.p_send.start()
        self.u.setup()
        self.u.load_browser()
        self.p_send.stop()
        self.p_cmd.stop()

        m_put = mock.Mock(name='uzbl_put')
        self.m_cmd.return_value.return_value.process.stdin.put = m_put

        self.u.browser_send('test_cmd')
        m_put.assert_called_once_with('test_cmd\n')

        self.u.browser_send('event TITLE 标题')
        m_put.assert_called_with('event TITLE \xe6\xa0\x87\xe9\xa2\x98\n')

    def test_dead(self):
        self.p_loadb.start()
        self.u.browser_send(None)
        self.m_loadb.assert_called_once()
        self.p_loadb.stop()


class TestBrowserClear(ViewerTestCase):
    def test_clear(self):
        with mock.patch.object(self.u, 'browser_url', mock.Mock()) as m_url:
            self.u.setup()
            self.u.browser_clear()
            m_url.assert_called_once()


class TestLoadBrowser(ViewerTestCase):
    def test_setup(self):
        self.u.setup()
        ok_(os.path.isdir(self.u.SCREENLY_HTML))

    def load_browser(self):
        m_uzbl = mock.Mock(name='uzbl')
        self.m_cmd.return_value = m_uzbl
        self.p_cmd.start()
        self.p_send.start()
        self.u.load_browser()
        self.p_send.stop()
        self.p_cmd.stop()
        self.m_cmd.assert_called_once_with('uzbl-browser')
        m_uzbl.assert_called_once_with(print_events=True, config='-', uri=None, _bg=True)
        m_send.assert_called_once()


class TestSignalHandlers(ViewerTestCase):
    def test_usr1(self):
        self.p_killall.start()
        eq_(None, self.u.sigusr1(None, None))
        self.m_killall.assert_called_once_with('omxplayer.bin', _ok_code=[1])
        self.p_killall.stop()


class TestWatchdog(ViewerTestCase):
    def test_watchdog_should_create_file_if_not_exists(self):
        try:
            os.remove(self.u.WATCHDOG_PATH)
        except:
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
