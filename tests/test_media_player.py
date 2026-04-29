import logging
import subprocess
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from viewer.media_player import (
    MPVMediaPlayer,
    MediaPlayerProxy,
    VLCMediaPlayer,
    get_alsa_audio_device,
)

logging.disable(logging.CRITICAL)


class TestMPVMediaPlayer(unittest.TestCase):
    def setUp(self) -> None:
        self.player = MPVMediaPlayer()

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

    @patch('viewer.media_player.subprocess.Popen')
    def test_play_invokes_popen_with_expected_args(
        self, mock_popen: Any
    ) -> None:
        self.player.set_asset('file:///test/video.mp4', 30)
        with patch.dict('os.environ', {'DEVICE_TYPE': 'pi5'}):
            self.player.play()

        mock_popen.assert_called_once_with(
            [
                'mpv',
                '--no-terminal',
                '--vo=drm',
                '--hwdec=auto-safe',
                '--log-file=/tmp/anthias-mpv.log',
                '--audio-device=alsa/default:CARD=vc4hdmi0',
                '--',
                'file:///test/video.mp4',
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @patch('viewer.media_player.subprocess.Popen')
    def test_play_uses_drm_copy_hwdec_on_pi4_64(self, mock_popen: Any) -> None:
        self.player.set_asset('file:///test/video.mp4', 30)
        with patch.dict('os.environ', {'DEVICE_TYPE': 'pi4-64'}):
            self.player.play()

        args, _ = mock_popen.call_args
        self.assertIn('--hwdec=drm-copy,auto-safe', args[0])

    @patch('viewer.media_player.subprocess.Popen')
    def test_play_uses_local_audio_device_when_configured(
        self, mock_popen: Any
    ) -> None:
        self.mock_settings.__getitem__.return_value = 'local'
        self.player.set_asset('file:///test/video.mp4', 30)
        self.player.play()

        args, _ = mock_popen.call_args
        self.assertIn('--audio-device=alsa/plughw:CARD=Headphones', args[0])

    @patch('viewer.media_player.subprocess.Popen')
    def test_play_reloads_settings_each_call(self, mock_popen: Any) -> None:
        self.player.set_asset('file:///test/video.mp4', 30)
        self.player.play()
        self.mock_settings.load.assert_called_once()

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


class TestGetAlsaAudioDevice(unittest.TestCase):
    def setUp(self) -> None:
        self.patch_settings = patch('viewer.media_player.settings')
        self.mock_settings = self.patch_settings.start()

    def tearDown(self) -> None:
        self.patch_settings.stop()

    def test_local_on_pi5_uses_hdmi_card(self) -> None:
        self.mock_settings.__getitem__.return_value = 'local'
        with patch('viewer.media_player.get_device_type', return_value='pi5'):
            self.assertEqual(get_alsa_audio_device(), 'default:CARD=vc4hdmi0')

    def test_local_on_other_pi_uses_headphones(self) -> None:
        self.mock_settings.__getitem__.return_value = 'local'
        for device_type in ['pi1', 'pi2', 'pi3', 'pi4']:
            with self.subTest(device_type=device_type):
                with patch(
                    'viewer.media_player.get_device_type',
                    return_value=device_type,
                ):
                    self.assertEqual(
                        get_alsa_audio_device(),
                        'plughw:CARD=Headphones',
                    )

    def test_hdmi_on_pi4_pi5_uses_vc4hdmi0(self) -> None:
        self.mock_settings.__getitem__.return_value = 'hdmi'
        for device_type in ['pi4', 'pi5']:
            with self.subTest(device_type=device_type):
                with patch(
                    'viewer.media_player.get_device_type',
                    return_value=device_type,
                ):
                    self.assertEqual(
                        get_alsa_audio_device(),
                        'default:CARD=vc4hdmi0',
                    )

    def test_hdmi_on_pi1_pi2_pi3_uses_vc4hdmi(self) -> None:
        self.mock_settings.__getitem__.return_value = 'hdmi'
        for device_type in ['pi1', 'pi2', 'pi3']:
            with self.subTest(device_type=device_type):
                with patch(
                    'viewer.media_player.get_device_type',
                    return_value=device_type,
                ):
                    self.assertEqual(
                        get_alsa_audio_device(), 'default:CARD=vc4hdmi'
                    )

    def test_hdmi_on_x86_falls_back_to_hid(self) -> None:
        self.mock_settings.__getitem__.return_value = 'hdmi'
        with patch('viewer.media_player.get_device_type', return_value='x86'):
            self.assertEqual(get_alsa_audio_device(), 'default:CARD=HID')


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
                with (
                    patch(
                        'viewer.media_player.get_device_type',
                        return_value=device_type,
                    ),
                    patch.dict('os.environ', {'DEVICE_TYPE': device_type}),
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

    def test_get_instance_returns_mpv_for_pi4_64(self) -> None:
        MediaPlayerProxy.INSTANCE = None
        with (
            patch('viewer.media_player.get_device_type', return_value='pi4'),
            patch.dict('os.environ', {'DEVICE_TYPE': 'pi4-64'}),
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
