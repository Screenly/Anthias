#!/usr/bin/env python
# -*- coding: utf8 -*-

from nose.tools import ok_, eq_
import mock

import os


class ViewerTestCase(object):
    def setup(self):
        import viewer
        self.u = viewer

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

    def test_usr2(self):
        self.u.last_settings_refresh = -1
        self.p_reload.start()
        eq_(None, self.u.sigusr2(None, None))
        self.m_reload.assert_called_once()
        self.p_reload.stop()
