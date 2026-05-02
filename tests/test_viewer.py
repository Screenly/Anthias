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

    def _stub_browser_stdout_static(
        self,
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
        type(browser_proc.process).stdout = mock.PropertyMock(
            return_value=value
        )

    def _stub_browser_stdout_chunks(
        self,
        browser_proc: mock.Mock,
        chunks: list[bytes],
    ) -> None:
        """As above, but advance through `chunks` across reads — for
        the success case where the handshake appears in a later poll."""
        type(browser_proc.process).stdout = mock.PropertyMock(
            side_effect=chunks
        )

    def test_load_browser(self) -> None:
        browser_proc = self.m_cmd.return_value.return_value
        # Two stdout reads: an empty buffer on the first poll, then the
        # handshake line appended on the second. Verifies that the
        # polling loop actually re-reads stdout each iteration.
        self._stub_browser_stdout_chunks(
            browser_proc,
            [b'starting up\n', b'starting up\nAnthias service start\n'],
        )
        browser_proc.is_alive.return_value = True
        self.p_cmd.start()
        self.p_sleep.start()
        try:
            self.u.load_browser()
        finally:
            self.p_sleep.stop()
            self.p_cmd.stop()
        self.m_cmd.assert_called_once_with('AnthiasWebview')

    def test_load_browser_raises_when_process_exits_before_handshake(
        self,
    ) -> None:
        browser_proc = self.m_cmd.return_value.return_value
        # The error message also reads stdout, so use the static stub
        # that returns the same value on every access rather than a
        # one-shot side_effect.
        self._stub_browser_stdout_static(browser_proc, b'')
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
        self._stub_browser_stdout_static(browser_proc, b'irrelevant noise')
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


class TestAssetDisplayability(ViewerTestCase):
    """
    The viewer used to call url_fails(uri) on every play, which blocked
    the loop for 1-15s on streams. Reachability is now owned by the
    server (celery beat sweep + on-demand recheck endpoint), and the
    viewer just consults Asset.is_reachable.
    """

    def test_skip_asset_check_short_circuits(self) -> None:
        """skip_asset_check=True means the operator opted out of any
        gating — display unconditionally, even if is_reachable=False."""
        asset = {
            'asset_id': 'a',
            'uri': 'http://example.com/x',
            'skip_asset_check': True,
            'is_reachable': False,
        }
        self.assertTrue(self.u._asset_is_displayable(asset))

    def test_local_file_existence_check(self) -> None:
        """Local URIs hit the filesystem directly (cheap, no roundtrip
        to the server's view of the world)."""
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as fh:
            local_path = fh.name
        try:
            self.assertTrue(
                self.u._asset_is_displayable(
                    {
                        'asset_id': 'a',
                        'uri': local_path,
                        'skip_asset_check': False,
                        'is_reachable': True,
                    }
                )
            )
        finally:
            os.unlink(local_path)
        # Same path, file gone.
        self.assertFalse(
            self.u._asset_is_displayable(
                {
                    'asset_id': 'a',
                    'uri': local_path,
                    'skip_asset_check': False,
                    'is_reachable': True,
                }
            )
        )

    def test_remote_uri_consults_is_reachable(self) -> None:
        ok = {
            'asset_id': 'a',
            'uri': 'http://example.com/x',
            'skip_asset_check': False,
            'is_reachable': True,
        }
        bad = {**ok, 'is_reachable': False}
        self.assertTrue(self.u._asset_is_displayable(ok))
        self.assertFalse(self.u._asset_is_displayable(bad))

    def test_missing_is_reachable_defaults_to_displayable(self) -> None:
        """Backstop for legacy rows / serializers that don't include the
        field. Don't silently freeze a playlist on an upgrade."""
        asset = {
            'asset_id': 'a',
            'uri': 'http://example.com/x',
            'skip_asset_check': False,
        }
        self.assertTrue(self.u._asset_is_displayable(asset))


class TestTriggerAssetRecheck(ViewerTestCase):
    def test_posts_to_recheck_endpoint(self) -> None:
        with mock.patch.object(self.u.requests, 'post') as m:
            self.u._trigger_asset_recheck('abc')
        m.assert_called_once()
        url = m.call_args.args[0]
        self.assertIn('/api/v2/assets/abc/recheck', url)

    def test_no_op_on_missing_asset_id(self) -> None:
        with mock.patch.object(self.u.requests, 'post') as m:
            self.u._trigger_asset_recheck(None)
        m.assert_not_called()

    def test_swallows_request_errors(self) -> None:
        """Best-effort: a server hiccup must not interrupt the asset loop."""
        import requests

        with mock.patch.object(
            self.u.requests,
            'post',
            side_effect=requests.ConnectionError('boom'),
        ):
            # Must not raise.
            self.u._trigger_asset_recheck('abc')


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
