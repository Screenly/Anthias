#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import os
from time import sleep
from unittest.mock import Mock, patch

import pytest

import viewer
from viewer.scheduling import Scheduler

logging.disable(logging.CRITICAL)


@pytest.fixture
def viewer_setup():
    original_splash_delay = viewer.SPLASH_DELAY
    viewer.SPLASH_DELAY = 0
    yield viewer
    viewer.SPLASH_DELAY = original_splash_delay


class TestEmptyPlaylist:
    @patch('viewer.constants.SERVER_WAIT_TIMEOUT', 0)
    def test_empty(self, viewer_setup):
        m_asset_list = Mock()
        m_asset_list.return_value = ([], None)
        with patch(
            'viewer.scheduling.generate_asset_list',
            m_asset_list,
        ):
            viewer_setup.scheduler = Scheduler()
            m_asset_list.assert_called_once()


class TestLoadBrowser:
    @patch('pydbus.SessionBus', Mock())
    def test_setup(self, viewer_setup):
        with patch.object(viewer_setup, 'load_browser'):
            viewer_setup.setup()

    def test_load_browser(self, viewer_setup):
        m_cmd = Mock(name='m_cmd')
        m_cmd.return_value.return_value.process.stdout = (
            b'Screenly service start'
        )
        with patch.object(viewer_setup.sh, 'Command', m_cmd):
            viewer_setup.load_browser()
        m_cmd.assert_called_once_with('ScreenlyWebview')


class TestWatchdog:
    def test_creates_file_if_not_exists(self, viewer_setup):
        try:
            os.remove(viewer_setup.utils.WATCHDOG_PATH)
        except OSError:
            pass
        viewer_setup.watchdog()
        assert os.path.exists(
            viewer_setup.utils.WATCHDOG_PATH
        )

    def test_updates_mtime(self, viewer_setup):
        viewer_setup.watchdog()
        mtime = os.path.getmtime(
            viewer_setup.utils.WATCHDOG_PATH
        )
        sleep(0.01)
        viewer_setup.watchdog()
        mtime2 = os.path.getmtime(
            viewer_setup.utils.WATCHDOG_PATH
        )
        assert mtime2 > mtime
