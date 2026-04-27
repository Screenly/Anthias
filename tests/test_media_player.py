import logging
import subprocess
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from viewer.media_player import (
    MPVMediaPlayer,
    MediaPlayerProxy,
    VLCMediaPlayer,
)

logging.disable(logging.CRITICAL)


class TestMPVMediaPlayer(unittest.TestCase):
    def setUp(self) -> None:
        self.player = MPVMediaPlayer()

    @patch('viewer.media_player.subprocess.Popen')
    def test_play_invokes_popen_with_expected_args(
        self, mock_popen: Any
    ) -> None:
        self.player.set_asset('file:///test/video.mp4', 30)
        self.player.play()

        mock_popen.assert_called_once_with(
            [
                'mpv',
                '--no-terminal',
                '--vo=drm',
                '--',
                'file:///test/video.mp4',
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @patch('viewer.media_player.subprocess.Popen')
    def test_is_playing_returns_true_when_process_running(
        self, mock_popen: Any
    ) -> None:
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        self.player.set_asset('file:///test/video.mp4', 30)
        self.player.play()

        self.assertTrue(self.player.is_playing())

    @patch('viewer.media_player.subprocess.Popen')
    def test_is_playing_returns_false_when_process_finished(
        self, mock_popen: Any
    ) -> None:
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        mock_popen.return_value = mock_process

        self.player.set_asset('file:///test/video.mp4', 30)
        self.player.play()

        self.assertFalse(self.player.is_playing())

    def test_is_playing_returns_false_when_no_process(self) -> None:
        self.assertFalse(self.player.is_playing())

    @patch('viewer.media_player.subprocess.Popen')
    def test_stop_terminates_process(self, mock_popen: Any) -> None:
        mock_process = MagicMock()
        mock_popen.return_value = mock_process

        self.player.set_asset('file:///test/video.mp4', 30)
        self.player.play()
        self.player.stop()

        mock_process.terminate.assert_called_once()
        self.assertIsNone(self.player.process)


class TestVLCMediaPlayer(unittest.TestCase):
    def setUp(self) -> None:
        with patch.object(VLCMediaPlayer, '__init__', return_value=None):
            self.player = VLCMediaPlayer()

        self.mock_media = MagicMock()
        self.mock_vlc_player = MagicMock()
        self.mock_vlc_player.get_media.return_value = self.mock_media
        self.player.player = self.mock_vlc_player

        self.patch_settings = patch('viewer.media_player.settings')
        self.patch_device_type = patch(
            'viewer.media_player.get_device_type', return_value='pi4'
        )

        self.mock_settings = self.patch_settings.start()
        self.mock_settings.__getitem__.return_value = 'hdmi'
        self.patch_device_type.start()

    def tearDown(self) -> None:
        self.patch_settings.stop()
        self.patch_device_type.stop()

    def test_set_asset_invokes_parse(self) -> None:
        self.player.set_asset('file:///test/video.mp4', 30)

        self.mock_vlc_player.get_media.assert_called_once()
        self.mock_media.parse.assert_called_once()


class TestMediaPlayerProxy(unittest.TestCase):
    def setUp(self) -> None:
        MediaPlayerProxy.INSTANCE = None

    def tearDown(self) -> None:
        MediaPlayerProxy.INSTANCE = None

    def test_get_instance_returns_vlc_for_pi_devices(self) -> None:
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

    def test_get_instance_returns_mpv_for_pi5_and_x86(self) -> None:
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
    def test_get_instance_returns_same_instance(self, _: Any) -> None:
        instance1 = MediaPlayerProxy.get_instance()
        instance2 = MediaPlayerProxy.get_instance()
        self.assertIs(instance1, instance2)


if __name__ == '__main__':
    unittest.main()
