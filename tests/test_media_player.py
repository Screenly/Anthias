import logging
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from viewer.media_player import (
    MPVMediaPlayer,
    MediaPlayerProxy,
    VLCMediaPlayer,
)

logging.disable(logging.CRITICAL)


class TestMPVMediaPlayer(unittest.TestCase):
    def setUp(self):
        self.player = MPVMediaPlayer()

    @patch('viewer.media_player.subprocess.Popen')
    def test_play_invokes_popen_with_expected_args(self, mock_popen):
        self.player.set_asset('file:///test/video.mp4', 30)
        self.player.play()

        mock_popen.assert_called_once_with(
            ['mpv', '--no-terminal', '--vo=drm', '--', 'file:///test/video.mp4'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @patch('viewer.media_player.subprocess.Popen')
    def test_is_playing_returns_true_when_process_running(self, mock_popen):
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        self.player.set_asset('file:///test/video.mp4', 30)
        self.player.play()

        self.assertTrue(self.player.is_playing())

    @patch('viewer.media_player.subprocess.Popen')
    def test_is_playing_returns_false_when_process_finished(self, mock_popen):
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        mock_popen.return_value = mock_process

        self.player.set_asset('file:///test/video.mp4', 30)
        self.player.play()

        self.assertFalse(self.player.is_playing())

    def test_is_playing_returns_false_when_no_process(self):
        self.assertFalse(self.player.is_playing())

    @patch('viewer.media_player.subprocess.Popen')
    def test_stop_terminates_process(self, mock_popen):
        mock_process = MagicMock()
        mock_popen.return_value = mock_process

        self.player.set_asset('file:///test/video.mp4', 30)
        self.player.play()
        self.player.stop()

        mock_process.terminate.assert_called_once()
        self.assertIsNone(self.player.process)


class TestMediaPlayerProxy(unittest.TestCase):
    def setUp(self):
        MediaPlayerProxy.INSTANCE = None

    def tearDown(self):
        MediaPlayerProxy.INSTANCE = None

    def test_get_instance_returns_vlc_for_pi_devices(self):
        for device_type in ['pi1', 'pi2', 'pi3', 'pi4']:
            with self.subTest(device_type=device_type):
                MediaPlayerProxy.INSTANCE = None
                with patch(
                    'viewer.media_player.get_device_type',
                    return_value=device_type,
                ):
                    with patch.object(
                        VLCMediaPlayer, '__init__', return_value=None
                    ):
                        instance = MediaPlayerProxy.get_instance()
                self.assertIsInstance(instance, VLCMediaPlayer)

    def test_get_instance_returns_mpv_for_pi5_and_x86(self):
        for device_type in ['pi5', 'x86']:
            with self.subTest(device_type=device_type):
                MediaPlayerProxy.INSTANCE = None
                with patch(
                    'viewer.media_player.get_device_type',
                    return_value=device_type,
                ):
                    instance = MediaPlayerProxy.get_instance()
                self.assertIsInstance(instance, MPVMediaPlayer)

    @patch('viewer.media_player.get_device_type', return_value='pi5')
    def test_get_instance_returns_same_instance(self, _):
        instance1 = MediaPlayerProxy.get_instance()
        instance2 = MediaPlayerProxy.get_instance()
        self.assertIs(instance1, instance2)


if __name__ == '__main__':
    unittest.main()
